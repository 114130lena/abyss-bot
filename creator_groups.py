"""Single source of truth for creator groups, Discord roles, and channel ownership."""
HANAYO = "hanayo"
SAKIHANE = "sakihane"
GROUP_ROLES = {HANAYO: "1563499860135772211", SAKIHANE: "1564279305171640361"}
CHZZK_GROUPS = {"36ddb9bb4f17593b60f1b63cec86611d": SAKIHANE}
YOUTUBE_GROUPS = {
    "UCHAr9pWGS30akL4MQCQT4uQ": HANAYO,
    "UC0YQnenKBCu5sGb7H61n6HA": SAKIHANE,
}
CAFE_MENU_GROUPS = {"431": HANAYO, "382": SAKIHANE}
X_USER_GROUPS = {"hanayohiyori": HANAYO}

def role_for(group):
    return GROUP_ROLES[group]

def chzzk_group(channel_id):
    # Existing two-channel bot uses Hanayo as the default; reject additions
    # explicitly in configuration before monitoring unrelated creators.
    return CHZZK_GROUPS.get(channel_id, HANAYO)

def youtube_group(channel_id, replay_id=""):
    if replay_id and channel_id == replay_id:
        return HANAYO
    return YOUTUBE_GROUPS.get(channel_id)

def cafe_group(menu_id):
    return CAFE_MENU_GROUPS.get(str(menu_id))

def x_group(username):
    return X_USER_GROUPS.get(username.casefold())
