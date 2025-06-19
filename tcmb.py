from fastapi import FastAPI, Query, HTTPException, Request
import requests
import xmltodict
from datetime import datetime, date, timedelta
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

ALLOWED_HOST = "tcmb-exchange-rates-api-tcmb-kuru.p.rapidapi.com"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def enforce_rapidapi_only(request: Request, call_next):
    # Header'larda x-rapidapi-host var mı kontrol et
    rapidapi_host = request.headers.get("x-rapidapi-host")
    if rapidapi_host != ALLOWED_HOST:
        raise HTTPException(status_code=403, detail="Access forbidden: Use via RapidAPI only.")
    return await call_next(request)

BASE_URL = "https://www.tcmb.gov.tr/kurlar"

def fetch_xml(date_param: datetime = None):
    today = date.today()

    # Eğer tarih bugünün tarihi ise today.xml kullan
    if date_param is None or date_param.date() == today:
        url = f"{BASE_URL}/today.xml"
    else:
        url = f"{BASE_URL}/{date_param.strftime('%Y%m')}/{date_param.strftime('%d%m%Y')}.xml"

    response = requests.get(url)
    if response.status_code != 200:
        return None
    return xmltodict.parse(response.content)


def find_currency(data, code: str):
    for currency in data['Tarih_Date']['Currency']:
        if currency['@CurrencyCode'] == code.upper():
            return {
                "code": currency['@CurrencyCode'],
                "name": currency['Isim'],
                "date": data['Tarih_Date']['@Tarih'],
                "forexBuying": float(currency['ForexBuying']) if currency['ForexBuying'] else None,
                "forexSelling": float(currency['ForexSelling']) if currency['ForexSelling'] else None,
                "banknoteBuying": float(currency['BanknoteBuying']) if currency['BanknoteBuying'] else None,
                "banknoteSelling": float(currency['BanknoteSelling']) if currency['BanknoteSelling'] else None
            }
    return None


@app.get("/")
def root():
    return {"message": "TCMB Kur API - /today, /currency?code=USD&date=2025-06-18"}


@app.get("/today")
def get_today_all():
    data = fetch_xml()
    return data['Tarih_Date']


@app.get("/currency")
def get_currency(code: str = Query(...), date: str = Query(None)):
    parsed_date = datetime.strptime(date, "%Y-%m-%d") if date else None
    data = fetch_xml(parsed_date)
    if not data:
        return {"error": "Kur verisi bulunamadı"}
    result = find_currency(data, code)
    return result or {"error": f"{code.upper()} kuru bulunamadı"}


@app.get("/diff")
def get_currency_diff(code: str = Query(...), days: int = Query(7)):
    today = datetime.today().date()
    start_day = today - timedelta(days=days)

    # Bugünkü veriyi çek
    data_today = fetch_xml(datetime.combine(today, datetime.min.time()))
    today_info = find_currency(
        data_today, code.upper()) if data_today else None

    # İlk günkü veriyi çek
    data_past = fetch_xml(datetime.combine(start_day, datetime.min.time()))
    past_info = find_currency(data_past, code.upper()) if data_past else None

    if not today_info or not past_info:
        raise HTTPException(
            status_code=404, detail="Currency data not available for given days")

    try:
        change = ((today_info['forexBuying'] -
                  past_info['forexBuying']) / past_info['forexBuying']) * 100
    except:
        raise HTTPException(
            status_code=500, detail="Could not calculate change")

    return {
        "code": code.upper(),
        "start_date": start_day.strftime("%Y-%m-%d"),
        "end_date": today.strftime("%Y-%m-%d"),
        "start_value": past_info['forexBuying'],
        "end_value": today_info['forexBuying'],
        "change_percent": round(change, 4)
    }


@app.get("/convert")
def convert_currency(
        from_currency: str = Query(..., alias="from"),
        to_currency: str = Query(..., alias="to"),
        amount: float = Query(...)):

    data = fetch_xml()
    if not data:
        raise HTTPException(status_code=503, detail="Kur verisi alınamadı")

    from_currency = from_currency.upper()
    to_currency = to_currency.upper()

    # TRY ise manuel setle
    if from_currency == "TRY":
        from_info = {"forexSelling": 1.0}
    else:
        from_info = find_currency(data, from_currency)
        if not from_info or from_info["forexSelling"] is None:
            raise HTTPException(
                status_code=404, detail=f"{from_currency} kuru bulunamadı")

    if to_currency == "TRY":
        to_info = {"forexBuying": 1.0}
    else:
        to_info = find_currency(data, to_currency)
        if not to_info or to_info["forexBuying"] is None:
            raise HTTPException(
                status_code=404, detail=f"{to_currency} kuru bulunamadı")

    # Hesaplama
    try_amount_in_try = amount * from_info["forexSelling"]
    converted = try_amount_in_try / to_info["forexBuying"]
    rate = from_info["forexSelling"] / to_info["forexBuying"]

    return {
        "from": from_currency,
        "to": to_currency,
        "amount": amount,
        "rate": round(rate, 4),
        "converted": round(converted, 2)
    }


@app.get("/history")
def get_currency_history(code: str = Query(...), days: int = Query(10)):
    code = code.upper()
    results = []
    checked = 0
    current = date.today()

    while len(results) < days and checked < days + 10:  # fazladan bak, çünkü tatil olabilir
        # Hafta sonlarını atla
        if current.weekday() < 5:
            data = fetch_xml(datetime.combine(current, datetime.min.time()))
            info = find_currency(data, code) if data else None

            if info and info["forexBuying"] and info["forexSelling"]:
                results.append({
                    "date": current.strftime("%Y-%m-%d"),
                    "buy": round(info["forexBuying"], 4),
                    "sell": round(info["forexSelling"], 4)
                })

        current -= timedelta(days=1)
        checked += 1

    if not results:
        raise HTTPException(status_code=404, detail="Hiç veri bulunamadı")

    return list(reversed(results))  # en eski → en yeni sırala


@app.get("/top-changes")
def get_top_changes(days: int = Query(7), count: int = Query(5)):
    currencies = [
        "USD", "EUR", "GBP", "CHF", "CAD", "SEK", "NOK", "JPY", "KWD", "SAR",
        "DKK", "AUD", "CNY", "BHD", "AZN", "RUB"
    ]

    today = date.today()
    start_day = get_previous_business_day(today - timedelta(days=days))

    data_today = fetch_xml(datetime.combine(today, datetime.min.time()))
    data_past = fetch_xml(datetime.combine(start_day, datetime.min.time()))

    if not data_today or not data_past:
        raise HTTPException(status_code=503, detail="Kur verisi alınamadı")

    changes = []
    for code in currencies:
        now = find_currency(data_today, code)
        past = find_currency(data_past, code)

        try:
            if now and past and now["forexBuying"] and past["forexBuying"]:
                change_pct = (
                    (now["forexBuying"] - past["forexBuying"]) / past["forexBuying"]) * 100
                changes.append({
                    "code": code,
                    "start": round(past["forexBuying"], 4),
                    "end": round(now["forexBuying"], 4),
                    "change_percent": round(change_pct, 4)
                })
        except:
            continue  # eksik veri varsa atla

    # Değişim oranına göre sırala
    sorted_changes = sorted(changes, key=lambda x: abs(
        x["change_percent"]), reverse=True)

    return sorted_changes[:count]


def get_previous_business_day(date_obj: date) -> date:
    while date_obj.weekday() >= 5:  # 5 = Cumartesi, 6 = Pazar
        date_obj -= timedelta(days=1)
    return date_obj
