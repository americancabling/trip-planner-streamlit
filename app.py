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

    Usernames are stored as keys; we will treat them case-insensitively.
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

    # ----------------- SESSION STATE FOR CURRENT TRIP -----------------
    if "current_trip" not in st.session_state:
        st.session_state["current_trip"] = new_empty_trip()
    if "selected_trip_name" not in st.session_state:
        st.session_state["selected_trip_name"] = "<New Trip>"
    if "itinerary_text" not in st.session_state:
        st.session_state["itinerary_text"] = ""

    # ----------------- SIDEBAR -----------------
    st.sidebar.title("Trips")
    st.sidebar.markdown(f"**Logged in as:** {current_user}")

    # Show which secrets keys exist (debug) and USERS contents
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

    # List saved trips in sidebar
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

    # ----------------- MAIN UI -----------------
    st.title("Road Trip Planner")

    tab_setup, tab_stops, tab_plan = st.tabs(
        ["🧭 Trip setup", "📍 Stops & ideas", "🧠 AI itinerary"]
    )

    # -----------------------------------------------------------------
    # TAB 1: Trip setup (manage trips, basic info, budget/preferences)
    # -----------------------------------------------------------------
    with tab_setup:
        st.subheader("0. Choose or manage a trip")

        trip = st.session_state["current_trip"]

        options = ["<New Trip>"] + trip_names
        try:
            default_index = options.index(st.session_state["selected_trip_name"])
        except ValueError:
            default_index = 0

        selected_name = st.selectbox(
            "Choose a saved trip (or start a new one)",
            options=options,
            index=default_index,
            help="Pick a previously saved trip, or '<New Trip>' to start fresh.",
        )

        # If user changed selection, load that trip into session
        if selected_name != st.session_state["selected_trip_name"]:
            st.session_state["selected_trip_name"] = selected_name
            if selected_name == "<New Trip>":
                st.session_state["current_trip"] = new_empty_trip()
            else:
                t = user_trips.get(selected_name, new_empty_trip())
                t["trip_name"] = selected_name
                st.session_state["current_trip"] = t
            trip = st.session_state["current_trip"]

        # Summary card
        with st.container():
            st.markdown("### 🧾 Current trip summary")
            st.markdown(
                f"""
                **Trip name:** {trip.get('trip_name') or '_Not named yet_'}  
                **From:** {trip.get('origin') or '—'}  
                **To:** {trip.get('destination') or '—'}  
                **Days:** {trip.get('total_days_available') or '—'}  
                """
            )
            st.markdown("---")

        col_manage1, col_manage2 = st.columns(2)
        with col_manage1:
            save_clicked = st.button("💾 Save this trip")
        with col_manage2:
            delete_clicked = st.button("🗑️ Delete this trip")

        if save_clicked:
            if not trip["trip_name"].strip():
                st.warning("Please enter a trip name in Section 1 before saving.")
            else:
                # Reload latest trips, just in case
                all_trips = load_all_trips()
                user_trips = get_user_trips(current_user, all_trips)

                base_name = trip["trip_name"].strip()
                unique_name = generate_unique_trip_name(
                    base_name, list(user_trips.keys())
                )
                trip["trip_name"] = unique_name

                user_trips[unique_name] = trip
                all_trips = set_user_trips(current_user, user_trips, all_trips)
                save_all_trips(all_trips)

                st.session_state["selected_trip_name"] = unique_name
                st.session_state["current_trip"] = trip

                st.success(f"Trip saved as: **{unique_name}**")
                st.rerun()

        if delete_clicked:
            if selected_name == "<New Trip>":
                st.warning(
                    "There is no saved trip to delete. Select a saved trip first."
                )
            else:
                # Reload latest trips, just in case
                all_trips = load_all_trips()
                user_trips = get_user_trips(current_user, all_trips)

                if selected_name in user_trips:
                    del user_trips[selected_name]
                    all_trips = set_user_trips(current_user, user_trips, all_trips)
                    save_all_trips(all_trips)

                    st.session_state["selected_trip_name"] = "<New Trip>"
                    st.session_state["current_trip"] = new_empty_trip()

                    st.success(f"Trip '{selected_name}' deleted.")
                    st.rerun()
                else:
                    st.error("Selected trip not found.")

        trip = st.session_state["current_trip"]
        st.markdown(
            f"_Currently editing:_ **{trip.get('trip_name') or '(unsaved trip)'}**"
        )

        # ---- Section 1: Basic Trip Info ----
        st.subheader("1. Basic Trip Info")

        trip["trip_name"] = st.text_input(
            "Trip name",
            value=trip.get("trip_name", ""),
            placeholder="e.g. Bowie to Miami – Scenic 12 days",
            help="Give this trip a name so you can save and find it later.",
        )

        col1, col2 = st.columns(2)
        with col1:
            trip["origin"] = st.text_input(
                "Where are you starting from?",
                value=trip.get("origin", ""),
                help="City and state, or a general starting area.",
            )
        with col2:
            trip["destination"] = st.text_input(
                "Where do you want to end up?",
                value=trip.get("destination", ""),
                help="City and state, or your main final destination.",
            )

        col3, col4, col5 = st.columns(3)
        with col3:
            trip["trip_direction"] = st.selectbox(
                "Trip type",
                options=["one_way", "round_trip"],
                format_func=lambda x: "One-way (I'll get back another way)"
                if x == "one_way"
                else "Round-trip (drive back to the start)",
                index=0
                if trip.get("trip_direction", "round_trip") == "one_way"
                else 1,
            )
        with col4:
            trip["total_days_available"] = st.number_input(
                "How many days do you have for this trip (total)?",
                min_value=1,
                max_value=90,
                value=int(trip.get("total_days_available", 10)),
            )
        with col5:
            trip["max_daily_drive_hours"] = st.number_input(
                "Max hours you're comfortable driving in a single day",
                min_value=1.0,
                max_value=12.0,
                step=0.5,
                value=float(trip.get("max_daily_drive_hours", 5.0)),
            )

        col6, col7 = st.columns(2)
        with col6:
            trip["driving_days_preference"] = st.selectbox(
                "How do you want to balance driving and activities each day?",
                options=["mostly_driving", "balanced", "mostly_activities"],
                format_func=lambda x: {
                    "mostly_driving": "Mostly driving (cover distance quickly)",
                    "balanced": "Balanced day (some driving, some exploring)",
                    "mostly_activities": "Mostly activities (shorter drives)",
                }[x],
                index=["mostly_driving", "balanced", "mostly_activities"].index(
                    trip.get("driving_days_preference", "balanced")
                ),
            )
        with col7:
            trip["overnight_stop_distance_style"] = st.selectbox(
                "How do you prefer to space your overnight stops?",
                options=[
                    "evenly_spread",
                    "push_far_on_first_day",
                    "short_first_day_then_even",
                ],
                format_func=lambda x: {
                    "evenly_spread": "Evenly spread out the driving",
                    "push_far_on_first_day": "Long first day, then shorter days",
                    "short_first_day_then_even": "Short first day, then even days",
                }[x],
                index=[
                    "evenly_spread",
                    "push_far_on_first_day",
                    "short_first_day_then_even",
                ].index(trip.get("overnight_stop_distance_style", "evenly_spread")),
            )

        # ---- Section 2: Budget & Preferences ----
        st.subheader("2. Budget & Preferences")

        colb1, colb2, colb3 = st.columns(3)
        with colb1:
            overall = st.number_input(
                "Rough total budget for the whole trip (optional, USD)",
                min_value=0.0,
                value=float(trip.get("overall_trip_budget") or 0.0),
            )
            trip["overall_trip_budget"] = overall or None

        with colb2:
            lodging_per_night = st.number_input(
                "Typical budget per night for places to stay (optional, USD)",
                min_value=0.0,
                value=float(trip.get("lodging_budget_per_night") or 0.0),
            )
            trip["lodging_budget_per_night"] = lodging_per_night or None

        with colb3:
            food_budget = st.number_input(
                "Typical food budget per person per day (optional, USD)",
                min_value=0.0,
                value=float(trip.get("food_budget_per_day_per_person") or 0.0),
            )
            trip["food_budget_per_day_per_person"] = food_budget or None

        colp1, colp2 = st.columns(2)
        with colp1:
            trip["lodging_style"] = st.selectbox(
                "What kind of places do you prefer to stay in?",
                options=["budget", "mid_range", "upscale", "luxury_resort"],
                format_func=lambda x: {
                    "budget": "Budget (simple & affordable)",
                    "mid_range": "Mid-range (comfortable, not fancy)",
                    "upscale": "Upscale (nicer hotels/resorts)",
                    "luxury_resort": "Luxury resort",
                }[x],
                index=["budget", "mid_range", "upscale", "luxury_resort"].index(
                    trip.get("lodging_style", "upscale")
                ),
            )
        with colp2:
            trip["travelers_description"] = st.text_input(
                "Who is going on this trip?",
                value=trip.get("travelers_description", ""),
                help="Example: '2 adults, no kids' or 'Family of 4 with teens'.",
            )

        trip["mobility_or_special_needs"] = st.text_area(
            "Any mobility needs or special considerations?",
            value=trip.get("mobility_or_special_needs", ""),
            help="Optional: accessibility needs, mobility limits, etc.",
        )

        st.markdown("**What kinds of things should the AI look for along the way?**")
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
            "Pick as many as you like:",
            options=display_options,
            default=display_default,
        )
        inverse_labels = {v: k for k, v in labels.items()}
        trip["auto_discovery_categories"] = [
            inverse_labels[d] for d in selected_display
        ]

        trip["default_max_detour_hours"] = st.number_input(
            "How far off the direct route (in extra hours of driving) are you willing to go for interesting stops?",
            min_value=0.0,
            max_value=6.0,
            step=0.5,
            value=float(trip.get("default_max_detour_hours", 2.0)),
        )

        trip["planning_focus"] = st.selectbox(
            "Do you care more about getting there fast, or seeing interesting things?",
            options=[
                "minimize_driving_time",
                "maximize_scenic_or_interesting_stops",
                "balanced",
            ],
            format_func=lambda x: {
                "minimize_driving_time": "Mainly minimize driving time",
                "maximize_scenic_or_interesting_stops": "Maximize scenic or interesting stops",
                "balanced": "Balance both",
            }[x],
            index=[
                "minimize_driving_time",
                "maximize_scenic_or_interesting_stops",
                "balanced",
            ].index(trip.get("planning_focus", "balanced")),
        )

        trip["output_detail_level"] = st.selectbox(
            "How detailed do you want the AI’s itinerary to be?",
            options=["high_level_overview", "daily_outline", "detailed_daily_plan"],
            format_func=lambda x: {
                "high_level_overview": "High-level overview",
                "daily_outline": "Daily outline",
                "detailed_daily_plan": "Very detailed day-by-day plan",
            }[x],
            index=[
                "high_level_overview",
                "daily_outline",
                "detailed_daily_plan",
            ].index(trip.get("output_detail_level", "daily_outline")),
        )

        # Save back to session
        st.session_state["current_trip"] = trip

    # -----------------------------------------------------------------
    # TAB 2: Stops & ideas (POIs)
    # -----------------------------------------------------------------
    with tab_stops:
        st.subheader("3. Points of Interest (optional)")

        trip = st.session_state["current_trip"]
        st.markdown(
            "Use these for specific ideas like **'Shopping'**, **'Fishing day'**, "
            "**'Visit Asheville, NC'**, or **'Michelin restaurant along the route'**.\n\n"
            "If you mark a stop as **Must do**, the AI must schedule it in the itinerary."
        )

        poi_list = trip.get("points_of_interest", [])

        # Show a simple summary list of stops added
        if poi_list:
            st.markdown("**Stops added:**")
            for i, poi in enumerate(poi_list, start=1):
                label = poi.get("label") or f"Stop {i}"
                prio = poi.get("priority", "nice_to_have")
                prio_text = "Must do" if prio == "must_do" else "Nice to have"
                st.markdown(f"- **Stop {i}: {label}** — {prio_text}")

            st.markdown("---")

        # Edit existing stops
        if poi_list:
            for i, poi in enumerate(poi_list):
                with st.expander(
                    f"Edit Stop {i+1}: {poi.get('label') or 'Edit this stop'}",
                    expanded=False,
                ):
                    poi["label"] = st.text_input(
                        f"Short title for this stop (Stop {i+1})",
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
                            "specific_stop": "A specific place (exact attraction, hotel, or restaurant)",
                            "city_or_region": "A city or area where I want options",
                            "category_along_route": "A type of stop the AI should look for along the route",
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
                        help="City or area if it applies (optional).",
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
                        f"How far off the main route would you go for this stop? (extra hours, optional, Stop {i+1})",
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
                        f"Minimum time you’d want to spend here (hours, optional, Stop {i+1})",
                        min_value=0.0,
                        max_value=72.0,
                        step=1.0,
                        value=float(poi.get("min_time_on_site_hours") or 0.0),
                        key=f"poi_time_{i}",
                    )

                    poi["priority"] = st.selectbox(
                        f"How important is this stop? (Stop {i+1})",
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
                        f"Delete this stop (Stop {i+1})", key=f"poi_del_{i}"
                    ):
                        poi_list.pop(i)
                        trip["points_of_interest"] = poi_list
                        st.session_state["current_trip"] = trip
                        st.rerun()

        # Add new stop
        st.markdown("---")
        st.markdown("**Add a new stop or idea**")

        new_label = st.text_input("Short title for this stop", key="new_poi_label")
        new_kind = st.selectbox(
            "What kind of idea is this?",
            options=["specific_stop", "city_or_region", "category_along_route"],
            format_func=lambda x: {
                "specific_stop": "A specific place (exact attraction, hotel, or restaurant)",
                "city_or_region": "A city or area where I want options",
                "category_along_route": "A type of stop the AI should look for along the route",
            }[x],
            key="new_poi_kind",
        )
        new_loc = st.text_input(
            "Where roughly is this? (optional)",
            key="new_poi_loc",
        )
        new_cat = st.text_input(
            "Category for this stop (optional)",
            key="new_poi_cat",
        )
        new_details = st.text_area(
            "Extra details about what you want here (optional)",
            key="new_poi_details",
        )
        new_detour = st.number_input(
            "How far off the main route would you go for this stop? (extra hours, optional)",
            min_value=0.0,
            max_value=6.0,
            step=0.5,
            key="new_poi_detour",
        )
        new_min_time = st.number_input(
            "Minimum time you’d want to spend here (hours, optional)",
            min_value=0.0,
            max_value=72.0,
            step=1.0,
            key="new_poi_min_time",
        )
        new_priority = st.selectbox(
            "How important is this stop?",
            options=["must_do", "nice_to_have"],
            format_func=lambda x: {
                "must_do": "Must do",
                "nice_to_have": "Nice to have",
            }[x],
            key="new_poi_priority",
        )

        if st.button("Add stop to this trip"):
            if new_label.strip():
                poi_list.append(
                    {
                        "label": new_label.strip(),
                        "poi_kind": new_kind,
                        "location_hint": new_loc.strip() or None,
                        "category": new_cat.strip() or None,
                        "details": new_details.strip() or None,
                        "max_detour_hours": float(new_detour)
                        if new_detour
                        else None,
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
                st.error("Please give the stop a short title.")

        st.session_state["current_trip"] = trip

    # -----------------------------------------------------------------
    # TAB 3: AI itinerary
    # -----------------------------------------------------------------
    with tab_plan:
        st.subheader("4. AI Trip Plan")

        trip = st.session_state["current_trip"]

        st.markdown(
            "When you're happy with the settings and stops in the other tabs, "
            "click the button below and the AI will design a day-by-day "
            "road-trip itinerary based on your preferences."
        )

        yaml_text = build_yaml_from_trip(trip)  # INTERNAL ONLY

        if st.button("Ask the AI to plan this trip"):
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
