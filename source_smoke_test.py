from config import ENABLE_GDELT, ENABLE_NEWSAPI, ENABLE_GUARDIAN
from sources import RSSSource, GDELTSource, NewsAPISource, GuardianSource

QUERY = '(oil OR gold OR fed OR inflation OR sanctions OR opec OR currency)'

def preview(label, items):
    print()
    print("==", label, "==")
    print("count:", len(items))
    for i, item in enumerate(items[:3], start=1):
        print(f"{i}. [{item.source_name}] {item.headline}")

def main():
    rss_items = RSSSource().fetch(max_records=12)
    preview("RSS", rss_items)

    if ENABLE_GDELT:
        try:
            gdelt_items = GDELTSource().fetch(query=QUERY, max_records=12)
            preview("GDELT", gdelt_items)
        except Exception as exc:
            print()
            print("== GDELT ==")
            print("error:", exc)

    if ENABLE_NEWSAPI:
        try:
            newsapi_items = NewsAPISource().fetch(query=QUERY, max_records=12)
            preview("NewsAPI", newsapi_items)
        except Exception as exc:
            print()
            print("== NewsAPI ==")
            print("error:", exc)
    else:
        print()
        print("== NewsAPI ==")
        print("skipped: NEWSAPI_KEY not set")

    if ENABLE_GUARDIAN:
        try:
            guardian_items = GuardianSource().fetch(query=QUERY, max_records=12)
            preview("Guardian", guardian_items)
        except Exception as exc:
            print()
            print("== Guardian ==")
            print("error:", exc)
    else:
        print()
        print("== Guardian ==")
        print("skipped: GUARDIAN_API_KEY not set")

if __name__ == "__main__":
    main()
