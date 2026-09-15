import pandas as pd
from strands import tool
from datetime import datetime, timedelta

def load_price_sheet(filepath: str) -> list[dict]:
    df = pd.read_excel(filepath)
    df.columns = [c.strip() for c in df.columns]
    return df.to_dict(orient="records")

@tool
def extract_product_keyword(text: str) -> str:
    """Extract the product keyword, size, and phase from an enquiry text (A&B Distribution Boards only for now)."""
    if not text:
        return ""
    lower = text.lower()

    if "distribution board" in lower:
        other_brands = ["abb", "havel", "haveel", "a&itech", "a&btech"]
        if any(b in lower for b in other_brands):
            return "unsupported_brand_distribution_board"

        base = "plastic base" if "plastic" in lower else "iron base" if "iron" in lower else "plastic base"
        size = next((s.upper() for s in ("d4", "d6", "d8") if s in lower), None)
        phase = "three phase" if any(p in lower for p in ("three phase", "triple phase")) else \
                "single phase" if "single phase" in lower else None

        parts = ["A&B distribution board", base]
        if size:
            parts.append(size.lower())
        if phase:
            parts.append(phase)
        return " ".join(parts)

    known_products = [
        "cable", "flood light", "solar", "knockout box",
        "conduit", "elcb", "socket", "switch", "bulb"
    ]
    for product in known_products:
        if product in lower:
            return product
    return ""

@tool
def find_price(product_asked: str, price_sheet_path: str = "prices.xlsx") -> float | None:
    """Look up the price of a product from the price worksheet."""
    if not product_asked:
        return None
    price_sheet = load_price_sheet(price_sheet_path)
    search_tokens = product_asked.lower().split()

    for row in price_sheet:
        desc = row.get("Description of goods")
        if not desc:
            continue
        desc_lower = str(desc).lower()
        if all(token in desc_lower for token in search_tokens):
            return row.get("price we sell at")
    return None

def get_unit_price(category: str) -> dict:
    """
    Resolves a category/keyword string to a unit price via find_price.
    Returns confident=False rather than guessing when no price is found.
    NOT a @tool — this is an internal helper called by recommend_restock,
    not something the agent calls directly.
    """
    price = find_price(category)
    if price is not None:
        return {"unit_price": price, "confident": True}
    return {"unit_price": None, "confident": False}

@tool
def map_keyword_to_tier(keyword: str) -> dict | None:
    """Map a product keyword to its qualification tier and threshold."""
    if not keyword:
        return None
    if "distribution board" in keyword:
        return {"category": "Distribution Board", "threshold": 35000}
    if keyword in ("conduit", "knockout box"):
        return {"category": "Box/Conduit", "threshold": 2500}
    if keyword in ("solar", "flood light"):
        return {"category": "Solar/Flood Light", "threshold": 34000}
    return None

@tool
def qualify_enquiry(tier_info: dict, estimated_value: float) -> dict:
    """Decide whether an enquiry meets its tier's qualification threshold."""
    if not tier_info:
        return {"qualified": False, "reason": "no tier mapping"}
    if estimated_value is None:
        return {"qualified": False, "reason": "no price found"}
    if estimated_value < tier_info["threshold"]:
        return {"qualified": False, "reason": "below threshold"}
    return {"qualified": True, "reason": "meets threshold", "category": tier_info["category"], "value": estimated_value}

@tool
def qualify_full_enquiry(text: str) -> dict:
    """Given raw enquiry text, extract product, find price, map tier, and qualify — all in one deterministic step."""
    keyword = extract_product_keyword(text)
    price = find_price(keyword)
    tier_info = map_keyword_to_tier(keyword)
    result = qualify_enquiry(tier_info, price)
    return {
        "extracted_keyword": keyword,
        "price": price,
        "tier_info": tier_info,
        **result,
    }

@tool
def check_stock_level(category: str) -> int | None:
    """Check current stock level for a category. Returns None if stock tracking isn't available."""
    # No stock data source exists yet — always returns None for now.
    # Future: query a real stock table/sheet when available.
    return None

@tool
def recommend_restock(category: str, weekly_enquiry_count: int, weekly_threshold: int = 15) -> dict:
    """Recommend a restock quantity, compute order value, and flag for auto-approval or owner escalation."""
    stock = check_stock_level(category)
    if stock is not None:
        return {"flagged": stock < weekly_threshold, "basis": "stock_level", "current_stock": stock}

    if weekly_enquiry_count < weekly_threshold:
        return {"flagged": False, "basis": "enquiry_volume", "reason": "below threshold"}

    recommended_quantity = weekly_enquiry_count  # stated 1:1 assumption, not a measured conversion rate
    price_info = get_unit_price(category)

    if not price_info["confident"]:
        result = {
            "flagged": True, "basis": "enquiry_volume",
            "recommended_quantity": recommended_quantity, "order_value": None,
            "decision": "needs_manual_pricing",
            "reason": f"{weekly_enquiry_count} enquiries this week; no price found for '{category}'"
        }
    else:
        order_value = recommended_quantity * price_info["unit_price"]
        decision = "escalated" if order_value >= 100000 else "auto_approved"
        result = {
            "flagged": True, "basis": "enquiry_volume",
            "recommended_quantity": recommended_quantity, "unit_price": price_info["unit_price"],
            "order_value": order_value, "decision": decision,
            "reason": f"{weekly_enquiry_count} enquiries this week (threshold: {weekly_threshold})"
        }

    from supabase import create_client
    import os
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))
    status_map = {"auto_approved": "auto_approved", "escalated": "pending", "needs_manual_pricing": "needs_pricing"}
    supabase.table("restock_drafts").insert({
        "product": category,
        "draft_amount": result.get("order_value"),
        "status": status_map[result["decision"]]
    }).execute()

    return result

@tool
def count_weekly_enquiries_by_category() -> dict:
    """Count A&B distribution board enquiries in the last 7 days, grouped by category."""
    from supabase import create_client
    import os

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")
    supabase = create_client(supabase_url, supabase_key)

    seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

    response = supabase.table("enquiries") \
        .select("product_asked, created_at") \
        .gte("created_at", seven_days_ago) \
        .execute()

    counts = {}
    for row in response.data:
        keyword = extract_product_keyword(row.get("product_asked", ""))
        if keyword and keyword != "unsupported_brand_distribution_board":
            counts[keyword] = counts.get(keyword, 0) + 1

    return counts