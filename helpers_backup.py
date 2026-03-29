from data import demo_articles

def filter_articles_by_field(field_name, value):
    filtered_articles = []

    for article in demo_articles:
        if article[field_name].lower() == value.lower():
            filtered_articles.append(article)

    return filtered_articles

def search_articles_by_headline(word):
    filtered_articles = []

    for article in demo_articles:
        if word.lower() in article["headline"].lower():
            filtered_articles.append(article)

    return filtered_articles

def get_article_by_id(article_id):
    if article_id < 1 or article_id > len(demo_articles):
        return None

    return demo_articles[article_id - 1]

def get_summary():
    europe_count = len(filter_articles_by_field("region", "Europe"))
    middle_east_count = len(filter_articles_by_field("region", "Middle East"))
    oil_count = len(filter_articles_by_field("topic", "oil"))
    sanctions_count = len(filter_articles_by_field("topic", "sanctions"))

    return {
        "total_articles": len(demo_articles),
        "europe_articles": europe_count,
        "middle_east_articles": middle_east_count,
        "oil_articles": oil_count,
        "sanctions_articles": sanctions_count
    }