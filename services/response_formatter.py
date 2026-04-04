import re

def clean_internal_text(text):
    if not text:
        return text
    text = re.sub(r'monitor:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'context unclear\s*—?\s*watch for follow-up confirmation\.?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\(\d+% confidence.*?\)', '', text)
    text = re.sub(r'\(weakened, velocity stable\)', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    try:
        text = text.encode().decode('unicode-escape')
    except:
        pass
    return text

def format_research_response(raw):
    cleaned = {
        "answer": clean_internal_text(raw.get("answer_card", {}).get("direct_answer", "")),
        "supporting_points": [
            clean_internal_text(p)
            for p in raw.get("answer_card", {}).get("supporting_points", [])
        ],
        "follow_up": raw.get("follow_up", []),
        "confidence": raw.get("confidence_pct", 0),
        "sources": raw.get("sources", [])
    }
    if not cleaned["supporting_points"]:
        del cleaned["supporting_points"]
    if not cleaned["follow_up"]:
        del cleaned["follow_up"]
    return cleaned
