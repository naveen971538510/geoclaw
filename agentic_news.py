import requests

def get_news(topic):
    resp = requests.post('http://127.0.0.1:8000/api/ask',
                         json={'question': topic})
    return resp.json()

def main():
    topics = ["What's new in AI?", "Latest tech trends", "Market news"]
    for topic in topics:
        print(f"\n=== {topic} ===")
        result = get_news(topic)
        print("Answer:", result.get('answer', ''))
        if 'supporting_points' in result:
            print("Details:")
            for p in result['supporting_points']:
                print(f"  - {p}")
        print("\nConfidence:", result.get('confidence', 0), "%")
        print("Sources:", result.get('sources', []))

if __name__ == '__main__':
    main()
