import yfinance as yf

tickers = ["AAPL", "GOOGL", "MSFT", "TSLA"]
data = yf.download(tickers, period="1mo", group_by="ticker")

data.to_csv("stocks.csv")
print("Saved to stocks.csv")
