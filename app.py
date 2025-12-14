from pathlib import Path
import json
import os

import streamlit as st
import yaml

# Try to import the OpenAI client (for ChatGPT integration)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    OPENAI_AVAILABLE = False

# ---------------------------------------------------------------------
# Paths and storage
# ---------------------------------------------------------------------
DATA_FILE = Path("saved_trips.json")


# ---------------------------------------------------------------------
# Helpers for trip storage (per-user)
# ---------------------------------------------------------------------
def load_all_trips() -> dict:
    """Load the entire trips structure from disk."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_all_trips(trips: dict) -> None:
    """Save the entire trips structure to disk."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(trips, f, indent=2)
    except Exception as e:
        st.error(f"Error saving trips: {e}")


def get_user_trips(username: str, all_trips: dict) -> dict:
    """Get trips for a single user (dict of name -> trip)."""
    return all_trips.get(username, {})


def set_user_trips(username: str, user_trips: dict, all_trips: dict) -> dict:
    """Set trips for a single user and return full structure."""
    all_trips[username] = user_trips
    return all_trips


def generate_unique_trip_name(base_name: str, existing_names: list[str]) -> str:
    """
    If base_name is not in existing_names, return it.
    Otherwise, append ' (1)', ' (2)', etc. until unique.
    """
    if base_name not in existing_names:
        return base_name

    counter = 1
    while True:
        candidate = f"{base_name} ({counter})"
        if candidate not in existing_names:
            return candidate
        counter += 1


# ---------------------------------------------------------------------
# Default empty trip template
# ---------------------------------------------------------------------
def new_empty_trip() -> dict:
    """Return a default trip dictionary with reasonable starter values."""
    return {
        "trip_name": "",
        "origin": "Bowie, MD",
        "destination": "Miami, FL",
        "trip_direction": "round_trip",
        "total_days_available": 10,
        "max_daily_drive_hours": 5.0,
        "driving_days_preference": "balanced",
        "overnight_stop_distance_style": "evenly_spread",
        "overall_trip_budget": None,
        "lodging_budget_per_night": None,
        "food_budget_per_day_per_person": None,
        "lodging_style": "upscale",
        "travelers_description": "2 adults, no kids",
        "mobility_or_special_needs": "",
        "auto_discovery_categories": [],
        "default_max_detour_hours": 2.0,
        "points_of_interest": [],
        "planning_focus": "balanced",
        "output_detail_level": "daily_outline",
    }


# ---------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------
def get_users_from_secrets():
    """
    Load USERS mapping from Streamlit secrets.

    Expected format in secrets (cloud or local):

    [USERS]
    tim = "some_password"
    buddy = "another_password"

    Usernames are stored as keys; we treat them case-insensitively.
    """
    try:
        users = st.secrets["USERS"]
        return users, None
    except Exception as e:
        return {}, (
            "No USERS configuration found in secrets. "
            "Add a [USERS] section in .streamlit/secrets.toml or Streamlit secrets. "
            f"Details: {e}"
        )


def authenticate():
    """
    Simple username/password login.
    Stores the logged-in username in st.session_state['current_user'].

    Usernames are case-insensitive and trimmed (e.g., ' Tim ' or 'TIM' -> 'tim').
    Passwords remain case-sensitive.
    """
    users_raw, err = get_users_from_secrets()
    if err:
        st.error(err)
        st.stop()

    # Normalize usernames to lowercase for matching
    normalized_users = {}
    try:
        for k in users_raw.keys():
            normalized_users[str(k).lower()] = str(users_raw[k])
    except Exception:
        normalized_users = {str(k).lower(): str(v) for k, v in dict(users_raw).items()}

    if "current_user" in st.session_state and st.session_state["current_user"]:
        return st.session_state["current_user"]

    st.title("Road Trip Planner – Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        uname = username.strip().lower()
        pwd = password.strip()

        if uname in normalized_users and pwd == normalized_users[uname]:
            st.session_state["current_user"] = uname
            st.success(f"Welcome, {uname}!")
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.stop()


# ---------------------------------------------------------------------
# YAML builder – internal only (never shown to user)
# ---------------------------------------------------------------------
def build_yaml_from_trip(trip: dict) -> str:
    """
    Build the internal YAML config that will be sent to the AI.

    The user never sees this YAML; it’s strictly for the model.
    """
    yaml_obj = {
        "version": "1.1",
        "agent_name": "roadtrip_trip_planner",
        "description": "User-provided configuration for a road-trip planner AI.",
        "trip_config": {
            "trip_name": trip.get("trip_name"),
            "origin": trip.get("origin"),
            "destination": trip.get("destination"),
            "trip_direction": trip.get("trip_direction", "round_trip"),
            "total_days_available": trip.get("total_days_available"),
            "max_daily_drive_hours": trip.get("max_daily_drive_hours"),
            "driving_days_preference": trip.get(
                "driving_days_preference", "balanced"
            ),
            "overnight_stop_distance_style": trip.get(
                "overnight_stop_distance_style", "evenly_spread"
            ),
            "overall_trip_budget": trip.get("overall_trip_budget"),
            "lodging_budget_per_night": trip.get("lodging_budget_per_night"),
            "food_budget_per_day_per_person": trip.get(
                "food_budget_per_day_per_person"
            ),
            "lodging_style": trip.get("lodging_style", "upscale"),
            "travelers_description": trip.get("travelers_description"),
            "mobility_or_special_needs": trip.get("mobility_or_special_needs"),
            "auto_discovery_categories": trip.get(
                "auto_discovery_categories", []
            ),
            "default_max_detour_hours": trip.get("default_max_detour_hours", 2),
            "points_of_interest": trip.get("points_of_interest", []),
            "planning_focus": trip.get("planning_focus", "balanced"),
            "output_detail_level": trip.get(
                "output_detail_level", "daily_outline"
            ),
        },
    }
    return yaml.dump(yaml_obj, sort_keys=False)


# ---------------------------------------------------------------------
# OpenAI / ChatGPT helper
# ---------------------------------------------------------------------
def get_openai_client():
    """
    Returns (client, error_message). If error_message is not None,
    ChatGPT calls should be disabled.
    """
    if not OPENAI_AVAILABLE:
        return (
            None,
            "openai library is not installed. Add 'openai' to requirements.txt.",
        )

    api_key = None

    # 1) Try top-level OPENAI_API_KEY
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
    except Exception:
        api_key = None

    # 2) Try nested under [USERS]
    if not api_key:
        try:
            users_section = st.secrets["USERS"]
            try:
                api_key = users_section["OPENAI_API_KEY"]
            except Exception:
                try:
                    api_key = users_section.get("OPENAI_API_KEY", None)
                except Exception:
                    api_key = None
        except Exception:
            api_key = None

    # 3) Fallback to environment variable
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        return (
            None,
            "OpenAI API key not set. Set OPENAI_API_KEY in .streamlit/secrets.toml, "
            "under [USERS] as USERS.OPENAI_API_KEY, or as an environment variable.",
        )

    try:
        client = OpenAI(api_key=api_key)
        return client, None
    except Exception as e:
        return None, f"Error creating OpenAI client: {e}"


def ask_chatgpt_for_itinerary(yaml_text: str) -> str:
    """
    Send the internal YAML config to the ChatGPT model,
    return a human-readable itinerary as text.
    """
    client, err = get_openai_client()
    if err:
        return f"(Trip planner AI disabled) {err}"

    system_prompt = (
        "You are an expert road-trip planner.\n"
        "The user will not see the YAML configuration you receive, "
        "but it fully describes their preferences for this trip.\n\n"
        "Your tasks:\n"
        "- Read the YAML carefully.\n"
        "- Design a realistic, day-by-day itinerary that respects:\n"
        "  - Maximum daily driving hours\n"
        "  - Total days available\n"
        "  - Trip direction (one-way vs round-trip)\n"
        "  - Points of interest and their priorities\n"
        "- Every point_of_interest whose priority is 'must_do' is MANDATORY:\n"
        "  - You MUST schedule a clear stop or activity that satisfies each must_do POI.\n"
        "  - Explicitly mention it in the itinerary using language that matches its label/details.\n"
        "  - If it is truly impossible to include due to time or route constraints,\n"
        "    explain briefly at the end why it could not be scheduled.\n"
        "- For major stops, include:\n"
        "  - Specific example hotel or lodging names that fit the lodging style\n"
        "  - Specific restaurant names, including at least one nice or special option per key stop\n"
        "  - Specific attractions or activities (museums, tours, viewpoints, hikes, historic sites, shopping, etc.)\n"
        "- When suggesting specific places (hotels, restaurants, activities, shopping):\n"
        "  - Prefer real, known places from current data.\n"
        "  - Mention the city/neighborhood and a short reason it fits.\n"
        "  - For shopping-related POIs (category like 'shopping' or details mentioning malls or department stores),\n"
        "    include at least one named shopping mall or retail district and clearly mark that time as shopping.\n"
        "  - You may mention key platforms or official websites for bookings,\n"
        "    but do not fabricate highly specific URLs.\n"
        "- At the end, include a brief reminder to double-check:\n"
        "  - Hotel prices and availability\n"
        "  - Restaurant hours and reservations\n"
        "  - Attraction opening hours\n"
        "  - Driving times and road conditions.\n\n"
        "Output:\n"
        "- A clear, human-readable itinerary (no YAML), grouped by day.\n"
        "- Each day should indicate:\n"
        "  - Start location and end location\n"
        "  - Driving time estimate\n"
        "  - Main stops or activities\n"
        "  - At least one suggested place to stay (where relevant)\n"
        "  - At least one suggested restaurant (where relevant)\n"
        "  - Any must_do POIs scheduled that day (call them out clearly).\n"
    )

    try:
        response = client.responses.create(
            model="gpt-5.1",
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": f"Here is the YAML config:\n```yaml\n{yaml_text}\n```",
                },
            ],
        )

        # Try to pull text content out of the response
        try:
            texts = []
            for item in response.output:
                for c in item.content:
                    if getattr(c, "type", None) == "output_text":
                        texts.append(c.text)
            if texts:
                return "\n".join(texts)
        except Exception:
            pass

        # Fallback: try the simple path
        try:
            return response.output[0].content[0].text
        except Exception:
            return f"Unexpected response format: {response}"
    except Exception as e:
        return f"Error calling trip planner AI: {e}"


# ---------------------------------------------------------------------
# Main Streamlit app
# ---------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Road Trip Planner", layout="wide")

    # ----------------- AUTH -----------------
    current_user = authenticate()  # stops if not logged in

    # ----------------- LOAD TRIPS FROM DISK -----------------
    all_trips = load_all_trips()
    user_trips = get_user_trips(current_user, all_trips)
    trip_names = sorted(user_trips.keys())

    # ----------------- SESSION STATE -----------------
    if "current_trip" not in st.session_state:
        st.session_state["current_trip"] = new_empty_trip()
    if "selected_trip_name" not in st.session_state:
        st.session_state["selected_trip_name"] = "<New Trip>"
    if "itinerary_text" not in st.session_state:
        st.session_state["itinerary_text"] = ""
    if "confirm_delete" not in st.session_state:
        st.session_state["confirm_delete"] = False

    trip = st.session_state["current_trip"]

    # ----------------- SIDEBAR -----------------
    st.sidebar.title("Trips")
    st.sidebar.markdown(f"**Logged in as:** {current_user}")

    # Debug info: secrets keys & USERS
    try:
        keys = list(st.secrets.keys())
        st.sidebar.caption(f"Secrets keys: {keys}")
        if "USERS" in keys:
            users_section = st.secrets["USERS"]
            try:
                user_keys = list(users_section.keys())
            except Exception:
                user_keys = str(users_section)
            st.sidebar.caption(f"USERS keys: {user_keys}")
    except Exception:
        st.sidebar.caption("Secrets not available.")

    # List saved trips
    if trip_names:
        st.sidebar.markdown("**Your saved trips:**")
        for name in trip_names:
            st.sidebar.markdown(f"- {name}")
    else:
        st.sidebar.markdown("_No saved trips yet._")

    # ChatGPT status
    _, client_err = get_openai_client()
    if client_err:
        st.sidebar.warning("Trip planner AI is not configured.\n\n" + client_err)
    else:
        st.sidebar.success("Trip planner AI is ready.")

    # ----------------- MAIN BODY -----------------
    st.title("Road Trip Planner")

    # How to use text
    st.markdown(
        """
**How to use this Planner:**

1. Give your trip a name (or work on an existing trip) and edit the **Trip Info**.
2. Add special **Points of Interest** and activities. Customize your adventure! All fields are optional.
3. Tell the **AI** to plan your trip.
"""
    )

    st.markdown("---")

    # ==========================
    # SECTION: Trip Info
    # ==========================
    st.subheader("Trip Info")

    # Row: Manage/create trip
    col_sel, col_save, col_delete = st.columns([4, 1, 1])
    with col_sel:
        options = ["<New Trip>"] + trip_names
        try:
            default_index = options.index(st.session_state["selected_trip_name"])
        except ValueError:
            default_index = 0

        selected_name = st.selectbox(
            "Manage / create trip",
            options=options,
            index=default_index,
            help="Choose an existing trip or '<New Trip>' to start a new one.",
        )

        if selected_name != st.session_state["selected_trip_name"]:
            st.session_state["selected_trip_name"] = selected_name
            if selected_name == "<New Trip>":
                st.session_state["current_trip"] = new_empty_trip()
            else:
                t = user_trips.get(selected_name, new_empty_trip())
                t["trip_name"] = selected_name
                st.session_state["current_trip"] = t
            trip = st.session_state["current_trip"]

    with col_save:
        save_clicked = st.button("💾 Save")

    with col_delete:
        delete_clicked = st.button("🗑️ Delete")

    # Save behavior
    if save_clicked:
        if not trip["trip_name"].strip():
            st.warning("Please enter a trip name before saving.")
        else:
            all_trips = load_all_trips()
            user_trips = get_user_trips(current_user, all_trips)

            base_name = trip["trip_name"].strip()
            unique_name = generate_unique_trip_name(base_name, list(user_trips.keys()))
            trip["trip_name"] = unique_name

            user_trips[unique_name] = trip
            all_trips = set_user_trips(current_user, user_trips, all_trips)
            save_all_trips(all_trips)

            st.session_state["selected_trip_name"] = unique_name
            st.session_state["current_trip"] = trip

            st.success(f"Trip saved as: **{unique_name}**")
            st.rerun()

    # Delete with confirmation
    if delete_clicked:
        if st.session_state["selected_trip_name"] == "<New Trip>":
            st.warning("There is no saved trip to delete. Select a saved trip first.")
        else:
            st.session_state["confirm_delete"] = True

    if st.session_state["confirm_delete"]:
        st.warning(
            f"Are you sure you want to delete the trip "
            f"'{st.session_state['selected_trip_name']}'? This cannot be undone."
        )
        col_cd1, col_cd2 = st.columns(2)
        with col_cd1:
            confirm = st.button("Yes, delete this trip")
        with col_cd2:
            cancel = st.button("Cancel")

        if confirm:
            selected_name = st.session_state["selected_trip_name"]
            all_trips = load_all_trips()
            user_trips = get_user_trips(current_user, all_trips)

            if selected_name in user_trips:
                del user_trips[selected_name]
                all_trips = set_user_trips(current_user, user_trips, all_trips)
                save_all_trips(all_trips)

                st.session_state["selected_trip_name"] = "<New Trip>"
                st.session_state["current_trip"] = new_empty_trip()
                st.session_state["confirm_delete"] = False

                st.success(f"Trip '{selected_name}' deleted.")
                st.rerun()
            else:
                st.error("Selected trip not found.")
                st.session_state["confirm_delete"] = False

        if cancel:
            st.session_state["confirm_delete"] = False
            st.info("Delete cancelled.")

    trip = st.session_state["current_trip"]
    st.markdown(
        f"_Currently editing:_ **{trip.get('trip_name') or '(unsaved trip)'}**"
    )

    # Row: Trip name / Starting / Destination / Trip type
    col_a1, col_a2, col_a3, col_a4 = st.columns([3, 3, 3, 2])
    with col_a1:
        trip["trip_name"] = st.text_input(
            "Trip name",
            value=trip.get("trip_name", ""),
            placeholder="e.g. Bowie to Miami – Scenic 12 days",
        )
    with col_a2:
        trip["origin"] = st.text_input(
            "Starting point",
            value=trip.get("origin", ""),
            help="City and state, or a general starting area.",
        )
    with col_a3:
        trip["destination"] = st.text_input(
            "Destination",
            value=trip.get("destination", ""),
            help="City and state, or your main final destination.",
        )
    with col_a4:
        trip["trip_direction"] = st.selectbox(
            "Trip type",
            options=["round_trip", "one_way"],
            format_func=lambda x: "Round trip" if x == "round_trip" else "One way",
            index=0
            if trip.get("trip_direction", "round_trip") == "round_trip"
            else 1,
        )

    # Row: Duration / Max driving / Driving-activity / Overnight stops
    col_b1, col_b2, col_b3, col_b4 = st.columns(4)
    with col_b1:
        trip["total_days_available"] = st.number_input(
            "Duration (in days)",
            min_value=1,
            max_value=90,
            value=int(trip.get("total_days_available", 10)),
        )
    with col_b2:
        trip["max_daily_drive_hours"] = st.number_input(
            "Max driving / day (hours)",
            min_value=1.0,
            max_value=12.0,
            step=0.5,
            value=float(trip.get("max_daily_drive_hours", 5.0)),
        )
    with col_b3:
        trip["driving_days_preference"] = st.selectbox(
            "Driving / Activity balance",
            options=["mostly_driving", "balanced", "mostly_activities"],
            format_func=lambda x: {
                "mostly_driving": "Mostly driving",
                "balanced": "Balanced",
                "mostly_activities": "Mostly activities",
            }[x],
            index=["mostly_driving", "balanced", "mostly_activities"].index(
                trip.get("driving_days_preference", "balanced")
            ),
        )
    with col_b4:
        trip["overnight_stop_distance_style"] = st.selectbox(
            "Overnight stops",
            options=[
                "evenly_spread",
                "push_far_on_first_day",
                "short_first_day_then_even",
            ],
            format_func=lambda x: {
                "evenly_spread": "Evenly spread out",
                "push_far_on_first_day": "Push far on day 1",
                "short_first_day_then_even": "Short day 1, then even",
            }[x],
            index=[
                "evenly_spread",
                "push_far_on_first_day",
                "short_first_day_then_even",
            ].index(trip.get("overnight_stop_distance_style", "evenly_spread")),
        )

    # Row: Budget / Room / Food / Hotel preference
    col_c1, col_c2, col_c3, col_c4 = st.columns(4)
    with col_c1:
        overall = st.number_input(
            "Total budget (USD)",
            min_value=0.0,
            value=float(trip.get("overall_trip_budget") or 0.0),
        )
        trip["overall_trip_budget"] = overall or None
    with col_c2:
        lodging_per_night = st.number_input(
            "Room rate max / night (USD)",
            min_value=0.0,
            value=float(trip.get("lodging_budget_per_night") or 0.0),
        )
        trip["lodging_budget_per_night"] = lodging_per_night or None
    with col_c3:
        food_budget = st.number_input(
            "Food budget / day / person (USD)",
            min_value=0.0,
            value=float(trip.get("food_budget_per_day_per_person") or 0.0),
        )
        trip["food_budget_per_day_per_person"] = food_budget or None
    with col_c4:
        trip["lodging_style"] = st.selectbox(
            "Hotel preference",
            options=["budget", "mid_range", "upscale", "luxury_resort"],
            format_func=lambda x: {
                "budget": "Budget",
                "mid_range": "Mid-range",
                "upscale": "Upscale",
                "luxury_resort": "Luxury resort",
            }[x],
            index=["budget", "mid_range", "upscale", "luxury_resort"].index(
                trip.get("lodging_style", "upscale")
            ),
        )

    # Row: Number travelers / description
    st.text_input(
        "Number of travelers (or short description)",
        value=trip.get("travelers_description", ""),
        key="travelers_desc_input",
        help="Example: '2 adults, no kids' or 'Family of 4 with teens'.",
    )
    trip["travelers_description"] = st.session_state["travelers_desc_input"]

    # Row: Special needs
    trip["mobility_or_special_needs"] = st.text_area(
        "Any mobility needs or special considerations?",
        value=trip.get("mobility_or_special_needs", ""),
    )

    # Row: Trip preferences (categories)
    st.markdown("**Trip preferences – what should the AI look for along the way?**")
    categories = [
        "michelin_star_dining",
        "other_high_end_dining",
        "historic_black_culture_sites",
        "museums_and_culture",
        "waterfalls",
        "hiking_trails",
        "beaches_or_ocean_access",
        "lakes_and_waterfronts",
        "scenic_drives_or_overlooks",
        "theme_parks",
        "nightlife",
        "golf",
    ]
    labels = {
        "michelin_star_dining": "Michelin-star or similar fine dining",
        "other_high_end_dining": "Other upscale restaurants",
        "historic_black_culture_sites": "Historic Black culture & civil rights sites",
        "museums_and_culture": "Museums & cultural stops",
        "waterfalls": "Waterfalls",
        "hiking_trails": "Hiking trails",
        "beaches_or_ocean_access": "Beaches and ocean access",
        "lakes_and_waterfronts": "Lakes, rivers, and waterfronts",
        "scenic_drives_or_overlooks": "Scenic drives & viewpoints",
        "theme_parks": "Theme parks",
        "nightlife": "Nightlife & bars",
        "golf": "Golf",
    }

    current_auto = trip.get("auto_discovery_categories", [])
    display_options = [labels[c] for c in categories]
    display_default = [labels[c] for c in current_auto if c in labels]

    selected_display = st.multiselect(
        "Trip preferences (select all that apply)",
        options=display_options,
        default=display_default,
    )
    inverse_labels = {v: k for k, v in labels.items()}
    trip["auto_discovery_categories"] = [inverse_labels[d] for d in selected_display]

    # Row: Max deviation / Trip style
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        trip["default_max_detour_hours"] = st.number_input(
            "Max hours willing to deviate off main route",
            min_value=0.0,
            max_value=6.0,
            step=0.5,
            value=float(trip.get("default_max_detour_hours", 2.0)),
        )
    with col_d2:
        trip["planning_focus"] = st.selectbox(
            "Overall trip style",
            options=[
                "minimize_driving_time",
                "maximize_scenic_or_interesting_stops",
                "balanced",
            ],
            format_func=lambda x: {
                "minimize_driving_time": "Fastest / efficient",
                "maximize_scenic_or_interesting_stops": "Scenic / interesting",
                "balanced": "Balanced",
            }[x],
            index=[
                "minimize_driving_time",
                "maximize_scenic_or_interesting_stops",
                "balanced",
            ].index(trip.get("planning_focus", "balanced")),
        )

    # Save updated trip in session
    st.session_state["current_trip"] = trip

    st.markdown("---")

    # ==========================
    # SECTION: Points of Interest
    # ==========================
    st.subheader("Points of Interest (optional)")

    st.markdown(
        """
Use these for specific ideas like **"Shopping"**, **"Fishing day"**, 
**"Visit Asheville, NC"**, or **"Michelin restaurant along the route"**.

If you mark a stop as **Must do**, the AI must schedule it in the itinerary.
"""
    )

    poi_list = trip.get("points_of_interest", [])

    # Show simple summary list
    if poi_list:
        st.markdown("**Stops added:**")
        for i, poi in enumerate(poi_list, start=1):
            label = poi.get("label") or f"Stop {i}"
            prio = poi.get("priority", "nice_to_have")
            prio_text = "Must do" if prio == "must_do" else "Nice to have"
            st.markdown(f"- **Stop {i}: {label}** — {prio_text}")
        st.markdown("---")

    # Edit existing stops (same functionality as before)
    if poi_list:
        for i, poi in enumerate(poi_list):
            with st.expander(
                f"Edit Stop {i+1}: {poi.get('label') or 'Edit this stop'}",
                expanded=False,
            ):
                poi["label"] = st.text_input(
                    f"Title for this stop (Stop {i+1})",
                    value=poi.get("label", ""),
                    key=f"poi_label_{i}",
                )

                poi["poi_kind"] = st.selectbox(
                    f"What kind of idea is this? (Stop {i+1})",
                    options=[
                        "specific_stop",
                        "city_or_region",
                        "category_along_route",
                    ],
                    format_func=lambda x: {
                        "specific_stop": "A specific place (hotel, restaurant, attraction)",
                        "city_or_region": "A city or area where I want options",
                        "category_along_route": "A type of stop along the route",
                    }[x],
                    index=[
                        "specific_stop",
                        "city_or_region",
                        "category_along_route",
                    ].index(poi.get("poi_kind", "city_or_region")),
                    key=f"poi_kind_{i}",
                )

                poi["location_hint"] = st.text_input(
                    f"Where roughly is this? (Stop {i+1})",
                    value=poi.get("location_hint", "") or "",
                    key=f"poi_loc_{i}",
                )

                poi["category"] = st.text_input(
                    f"Category for this stop (optional, Stop {i+1})",
                    value=poi.get("category", "") or "",
                    key=f"poi_cat_{i}",
                    help="Example: 'high_end_shopping', 'waterfall', 'historic_black_tour'.",
                )

                poi["details"] = st.text_area(
                    f"Extra details about what you want here (optional, Stop {i+1})",
                    value=poi.get("details", "") or "",
                    key=f"poi_details_{i}",
                )

                poi["max_detour_hours"] = st.number_input(
                    f"Max deviation (hours) for this stop (optional, Stop {i+1})",
                    min_value=0.0,
                    max_value=6.0,
                    step=0.5,
                    value=float(
                        poi.get("max_detour_hours")
                        or trip.get("default_max_detour_hours", 2.0)
                    ),
                    key=f"poi_detour_{i}",
                )

                poi["min_time_on_site_hours"] = st.number_input(
                    f"Time allotted at stop (hours, optional, Stop {i+1})",
                    min_value=0.0,
                    max_value=72.0,
                    step=1.0,
                    value=float(poi.get("min_time_on_site_hours") or 0.0),
                    key=f"poi_time_{i}",
                )

                poi["priority"] = st.selectbox(
                    f"Importance of stop (Stop {i+1})",
                    options=["must_do", "nice_to_have"],
                    format_func=lambda x: {
                        "must_do": "Must do",
                        "nice_to_have": "Nice to have",
                    }[x],
                    index=["must_do", "nice_to_have"].index(
                        poi.get("priority", "nice_to_have")
                    ),
                    key=f"poi_prio_{i}",
                )

                if st.button(
                    f"Delete this stop (Stop {i+1})",
                    key=f"poi_del_{i}",
                ):
                    poi_list.pop(i)
                    trip["points_of_interest"] = poi_list
                    st.session_state["current_trip"] = trip
                    st.rerun()

    # Add new stop – rows as you specified
    st.markdown("---")
    st.markdown("**Add a new stop**")

    new_label = st.text_input("Title for this stop", key="new_poi_label")

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        new_kind = st.selectbox(
            "What kind of idea is this?",
            options=["specific_stop", "city_or_region", "category_along_route"],
            format_func=lambda x: {
                "specific_stop": "A specific place (hotel, restaurant, attraction)",
                "city_or_region": "A city or general area",
                "category_along_route": "A type of stop the AI should look for",
            }[x],
            key="new_poi_kind",
        )
    with col_p2:
        new_loc = st.text_input(
            "Where roughly is this? (optional)",
            key="new_poi_loc",
        )
    with col_p3:
        new_cat = st.text_input(
            "Category for this stop (optional)",
            key="new_poi_cat",
        )

    new_details = st.text_area(
        "Extra details about what you want here (optional)",
        key="new_poi_details",
    )

    col_p4, col_p5, col_p6 = st.columns(3)
    with col_p4:
        new_detour = st.number_input(
            "Max deviation (hours)",
            min_value=0.0,
            max_value=6.0,
            step=0.5,
            key="new_poi_detour",
        )
    with col_p5:
        new_min_time = st.number_input(
            "Time allotted at stop (hours)",
            min_value=0.0,
            max_value=72.0,
            step=1.0,
            key="new_poi_min_time",
        )
    with col_p6:
        new_priority = st.selectbox(
            "Importance of stop",
            options=["must_do", "nice_to_have"],
            format_func=lambda x: {
                "must_do": "Must do",
                "nice_to_have": "Nice to have",
            }[x],
            key="new_poi_priority",
        )

    if st.button("Add this stop"):
        if new_label.strip():
            poi_list.append(
                {
                    "label": new_label.strip(),
                    "poi_kind": new_kind,
                    "location_hint": new_loc.strip() or None,
                    "category": new_cat.strip() or None,
                    "details": new_details.strip() or None,
                    "max_detour_hours": float(new_detour) if new_detour else None,
                    "min_time_on_site_hours": float(new_min_time)
                    if new_min_time
                    else None,
                    "priority": new_priority,
                }
            )
            trip["points_of_interest"] = poi_list
            st.session_state["current_trip"] = trip
            st.success("Stop added.")
            st.rerun()
        else:
            st.error("Please give the stop a title.")

    st.session_state["current_trip"] = trip

    st.markdown("---")

    # ==========================
    # SECTION: AI Itinerary
    # ==========================
    st.subheader("AI Itinerary")

    col_ai1, col_ai2 = st.columns(2)
    with col_ai1:
        trip["output_detail_level"] = st.selectbox(
            "Itinerary detail level",
            options=["high_level_overview", "daily_outline", "detailed_daily_plan"],
            format_func=lambda x: {
                "high_level_overview": "High-level overview",
                "daily_outline": "Daily outline",
                "detailed_daily_plan": "Detailed day-by-day plan",
            }[x],
            index=[
                "high_level_overview",
                "daily_outline",
                "detailed_daily_plan",
            ].index(trip.get("output_detail_level", "daily_outline")),
        )
    with col_ai2:
        st.markdown("&nbsp;")  # spacing
        ask_ai = st.button("Ask AI to plan this trip")

    st.session_state["current_trip"] = trip

    yaml_text = build_yaml_from_trip(trip)

    if ask_ai:
        with st.spinner("Asking the trip planner AI to design your route..."):
            itinerary = ask_chatgpt_for_itinerary(yaml_text)
            st.session_state["itinerary_text"] = itinerary

    if st.session_state["itinerary_text"]:
        st.markdown("### AI-Generated Itinerary")
        itinerary_text = st.session_state["itinerary_text"]

        st.text_area(
            "Itinerary",
            value=itinerary_text,
            height=400,
        )

        st.markdown("**Copy and paste itinerary to your email or notes.**")


if __name__ == "__main__":
    main()
