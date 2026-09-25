import concurrent.futures
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
import requests

app = FastAPI(
    title="Dish Price Comparison Agent",
    description="Finds restaurants near coordinates, searches for a chosen dish, and sorts them by price.",
    version="1.0"
)

# Standard browser headers required for public endpoints
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.swiggy.com/",
}

BASE_URL = "https://www.swiggy.com/dapi"


def fetch_nearby_restaurants(lat: float, lng: float) -> List[Dict[str, Any]]:
    """Step 1: Get all restaurants near the user's latitude and longitude."""
    url = f"{BASE_URL}/restaurants/list/v5?lat={lat}&lng={lng}&page_type=DESKTOP_WEB_LISTING"
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return []
            
        data = res.json()
        cards = data.get("data", {}).get("cards", [])
        
        restaurants = []
        for card in cards:
            # Parse grid cards to extract restaurant info
            grid_elements = card.get("card", {}).get("card", {}).get("gridElements", {}).get("infoWithStyle", {}).get("restaurants", [])
            for r in grid_elements:
                info = r.get("info", {})
                if info:
                    restaurants.append({
                        "id": info.get("id"),
                        "name": info.get("name"),
                        "rating": info.get("avgRating"),
                        "area": info.get("areaName")
                    })
        return restaurants
    except Exception:
        return []


def fetch_and_extract_dish(restaurant: Dict[str, Any], lat: float, lng: float, dish_name: str) -> List[Dict[str, Any]]:
    """Step 2: Inspect a restaurant's menu for the specified dish."""
    res_id = restaurant["id"]
    url = f"{BASE_URL}/menu/pl?page-type=REGULAR_MENU&complete-menu=true&lat={lat}&lng={lng}&restaurantId={res_id}"
    
    matches = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code != 200:
            return matches

        data = res.json()
        cards = (
            data.get("data", {})
            .get("cards", [])[-1]
            .get("groupedCard", {})
            .get("cardGroupMap", {})
            .get("REGULAR", {})
            .get("cards", [])
        )

        for c in cards:
            item_cards = c.get("card", {}).get("card", {}).get("itemCards", [])
            for item in item_cards:
                info = item.get("card", {}).get("info", {})
                name = info.get("name", "")

                # Perform case-insensitive match for the requested dish
                if dish_name.lower() in name.lower():
                    # Price is returned in paise (e.g., 15000 = Rs. 150)
                    price = info.get("price") or info.get("defaultPrice") or 0
                    price_in_rs = price / 100.0

                    if price_in_rs > 0:
                        matches.append({
                            "restaurant_name": restaurant["name"],
                            "area": restaurant["area"],
                            "rating": restaurant["rating"],
                            "dish_matched": name,
                            "price": price_in_rs,
                            "currency": "INR"
                        })
    except Exception:
        pass
        
    return matches


@app.get("/agent/compare-dish")
def compare_dish_prices(
    lat: float = Query(..., description="Your Latitude (e.g., 13.0827 for Chennai)"),
    lng: float = Query(..., description="Your Longitude (e.g., 80.2707 for Chennai)"),
    dish: str = Query(..., description="The dish you want to find (e.g., Biryani, Paneer Butter Masala, Pizza)")
):
    """
    Main Agent Endpoint:
    1. Locates nearby restaurants at (lat, lng).
    2. Searches menus concurrently for the specified dish.
    3. Sorts results from cheapest to most expensive.
    """
    # 1. Fetch nearby restaurants
    restaurants = fetch_nearby_restaurants(lat, lng)
    if not restaurants:
        raise HTTPException(
            status_code=444, 
            detail="No restaurants found near these coordinates."
        )

    # 2. Concurrently fetch menus to keep response times fast
    all_matched_dishes = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(fetch_and_extract_dish, r, lat, lng, dish) 
            for r in restaurants[:15]  # Limits search to top 15 nearby restaurants for speed
        ]
        for future in concurrent.futures.as_completed(futures):
            all_matched_dishes.extend(future.result())

    if not all_matched_dishes:
        return {
            "status": "success",
            "message": f"No restaurants nearby have '{dish}' available on their menu.",
            "total_matches": 0,
            "results": []
        }

    # 3. Sort results by price (Low to High)
    sorted_dishes = sorted(all_matched_dishes, key=lambda x: x["price"])

    return {
        "status": "success",
        "search_query": dish,
        "location": {"latitude": lat, "longitude": lng},
        "total_options_found": len(sorted_dishes),
        "results_sorted_by_price": sorted_dishes
    }
