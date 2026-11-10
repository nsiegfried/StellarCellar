import json
import os
import re

import anthropic

SYSTEM = (
    "You are a household sommelier. Choose wines ONLY from the candidate list, using their codes. "
    "Prefer bottles marked drink_soon when they fit. Never invent codes or wines. "
    "Respond with JSON only, no prose and no code fences, in this shape: "
    '{"summary": "one or two sentences", "picks": [{"code": "...", "reason": "under 20 words"}]}. '
    "Return at most 3 picks."
)


def _fallback(cands):
    order = sorted(cands, key=lambda c: (c["status"] != "drink_soon", c["window_to"] or 9999))
    picks = []
    for c in order[:3]:
        if c["status"] == "drink_soon":
            reason = "Drink soon, use it up"
        elif c["status"] == "unknown":
            reason = "No drinking window on record, check the label"
        else:
            reason = "In its drinking window now"
        picks.append({"code": c["code"], "label": c["label"], "status": c["status"],
                      "location": c["location"], "reason": reason})
    return {"summary": "Quick picks from your cellar.", "picks": picks, "source": "rules"}


def recommend(food, occasion, cands, taste_profile: str = ""):
    if not cands:
        return {"summary": "Nothing ready in your cellar fits that. Check Use soon or change the budget.",
                "picks": [], "source": "none"}
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return _fallback(cands)

    by_code = {c["code"]: c for c in cands}
    listing = [
        {"code": c["code"], "label": c["label"], "status": c["status"],
         "price": c["price"], "color": c["color"], "location": c["location"],
         "household_score": c.get("score")}
        for c in cands[:60]
    ]
    user = json.dumps({"food": food, "occasion": occasion, "candidates": listing})
    system = SYSTEM
    if taste_profile:
        system += (" The household's taste profile follows; weigh it heavily and prefer candidates with a high "
                   "household_score (100-point, their own past rating). Profile: " + taste_profile[:4000])
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=os.environ.get("CELLAR_MODEL", "claude-sonnet-5-5"),
            max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(block, "text", "") for block in resp.content)
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception:
        return _fallback(cands)

    picks = []
    for p in data.get("picks", []):
        c = by_code.get(p.get("code"))  # reject any code not in the shortlist
        if not c:
            continue
        picks.append({"code": c["code"], "label": c["label"], "status": c["status"],
                      "location": c["location"], "reason": str(p.get("reason", ""))[:200]})
        if len(picks) == 3:
            break
    if not picks:
        return _fallback(cands)
    return {"summary": str(data.get("summary", ""))[:400], "picks": picks, "source": "claude"}


LABEL_SYSTEM = (
    "You read wine bottle labels. Return JSON only, no prose and no code fences, in this shape: "
    '{"producer": "", "name": "", "vintage": null, "color": "red|white|rose|sparkling|dessert|fortified", '
    '"region": "", "country": "", "varietals": [], "drink_from": null, "drink_to": null, '
    '"confidence": "high|medium|low", "note": ""}. '
    "Use only what the label shows for producer, name, vintage, region and country; leave a field empty "
    "or null if it is not visible. drink_from and drink_to are your best estimate of the drinking window "
    "as years, and may be null if you are unsure. Set confidence low if the text is unclear. "
    "Put anything the owner should double check in note, under 20 words."
)

FIELDS = ("producer", "name", "vintage", "color", "region", "country", "varietals",
          "drink_from", "drink_to", "confidence", "note")


def read_label(image_b64: str, media_type: str):
    """Extract wine details from a label photo. Returns (data, error)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, "Label scanning needs an Anthropic API key in the add-on options."
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=os.environ.get("CELLAR_MODEL", "claude-sonnet-5-5"),
            max_tokens=400,
            system=LABEL_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                              "data": image_b64}},
                {"type": "text", "text": "Read this wine label."},
            ]}],
        )
        text = "".join(getattr(block, "text", "") for block in resp.content)
        raw = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception as exc:  # network, auth, or unparseable reply
        return None, f"Could not read the label ({type(exc).__name__}). Try again or type it in."

    data = {k: raw.get(k) for k in FIELDS}
    for k in ("vintage", "drink_from", "drink_to"):
        try:
            data[k] = int(data[k]) if data[k] not in (None, "", 0) else None
        except (TypeError, ValueError):
            data[k] = None
    if data["color"] not in ("red", "white", "rose", "sparkling", "dessert", "fortified"):
        data["color"] = "red"
    if data["confidence"] not in ("high", "medium", "low"):
        data["confidence"] = "low"
    data["varietals"] = [str(v) for v in (data["varietals"] or [])][:5]
    for k in ("producer", "name", "region", "country", "note"):
        data[k] = str(data[k] or "")[:120]
    return data, None


REGION_SYSTEM = (
    "You identify where wines come from. For each wine in the list, give the most specific well-known wine "
    "region (an appellation or valley, e.g. 'Paso Robles', 'Napa Valley', 'Mendoza', 'Rioja') and the country. "
    "Use your knowledge of the producer; do not guess wildly. Respond with JSON only, no prose and no fences: "
    '{"wines": [{"id": 1, "region": "", "country": "", "confidence": "high|medium|low"}]}. '
    "Use confidence low, and leave region empty, when you are not reasonably sure."
)

ABOUT_SYSTEM = (
    "You write two short sentences about a wine region for a home wine app: where it is and what about the "
    "place (climate, soil, geography) shapes its wines. Plain, friendly, no hype, no markdown, under 55 words."
)


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return anthropic.Anthropic(api_key=key) if key else None


def suggest_regions(wines):
    """wines: [{id, label}] -> ([{id, region, country, confidence}], error)"""
    client = _client()
    if not client:
        return None, "Region lookup needs an Anthropic API key in the add-on options."
    try:
        resp = client.messages.create(
            model=os.environ.get("CELLAR_MODEL", "claude-sonnet-5-5"), max_tokens=2000, system=REGION_SYSTEM,
            messages=[{"role": "user", "content": json.dumps({"wines": wines[:80]})}])
        text = "".join(getattr(b, "text", "") for b in resp.content)
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception as exc:
        return None, f"Region lookup failed ({type(exc).__name__})."
    out = []
    for w in data.get("wines", []):
        try:
            out.append({"id": int(w.get("id")), "region": str(w.get("region") or "")[:60],
                        "country": str(w.get("country") or "")[:40],
                        "confidence": w.get("confidence") if w.get("confidence") in ("high", "medium", "low") else "low"})
        except (TypeError, ValueError):
            continue
    return out, None


def about_region(region: str, country: str):
    client = _client()
    if not client:
        return None
    try:
        resp = client.messages.create(
            model=os.environ.get("CELLAR_MODEL", "claude-sonnet-5-5"), max_tokens=200, system=ABOUT_SYSTEM,
            messages=[{"role": "user", "content": f"{region}, {country}".strip(", ")}])
        return "".join(getattr(b, "text", "") for b in resp.content).strip()[:400]
    except Exception:
        return None
