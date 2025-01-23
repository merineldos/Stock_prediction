import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import torch
import joblib
from tenacity import retry, stop_after_attempt, wait_fixed
from news import fetch_and_return_articles  # Your function to fetch news articles
import math
from decimal import Decimal, ROUND_DOWN
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Retry logic for loading sentiment analysis model and vectorizer
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))  # Retry 3 times with 5-second intervals
def load_model_and_vectorizer():
    try:
        sentiment_model = joblib.load('model/Latest_Model.joblib')  # Replace with actual path
        vectorizer = joblib.load('model/improved_tfidf_vectorizer.joblib')  # Replace with actual path
        return sentiment_model, vectorizer
    except Exception as e:
        logger.error(f"Error loading sentiment model or vectorizer: {e}")
        raise  # Retry if loading fails

sentiment_model, vectorizer = None, None
try:
    sentiment_model, vectorizer = load_model_and_vectorizer()
except Exception as e:
    logger.error("Failed to load sentiment model or vectorizer after retries.")

# Load the summarization model directly
try:
    tokenizer = AutoTokenizer.from_pretrained("MBZUAI/LaMini-Flan-T5-248M")
    model = AutoModelForSeq2SeqLM.from_pretrained("MBZUAI/LaMini-Flan-T5-248M")
    logger.info("Summarization model loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load summarization model: {e}")
    tokenizer = None
    model = None

# Data model for input
class CompanyRequest(BaseModel):
    company_name: str

# Normalize sentiment score to a scale of 0-10
def normalize_sentiment_score(score, min_score=-0.3, max_score=0.45):
    try:
        return (score - min_score) / (max_score - min_score) * 10
    except ZeroDivisionError:
        logger.error(f"ZeroDivisionError in normalize_sentiment_score with score: {score}")
        return 0

# Get sentiment score
def get_sentiment_score(text):
    if not sentiment_model or not vectorizer:
        logger.error("Sentiment model or vectorizer is not loaded.")
        return 0
    try:
        vectorized_text = vectorizer.transform([text])
        sentiment_score = sentiment_model.predict(vectorized_text)[0]
        return sentiment_score
    except Exception as e:
        logger.error(f"Error predicting sentiment: {e}")
        return 0

# Summarize articles using the loaded model
def summarize_articles_together(articles):
    if not tokenizer or not model:
        logger.error("Summarizer model or tokenizer is not available.")
        return "Summarizer not available."

    # Prepare input for summarizer
    combined_text = " ".join(articles)
    inputs = tokenizer(combined_text, return_tensors="pt", max_length=1024, truncation=True)

    try:
        summary_ids = model.generate(inputs["input_ids"], max_length=130, min_length=80, length_penalty=2.0, num_beams=4, early_stopping=True)
        summary = tokenizer.decode(summary_ids[0], skip_special_tokens=True)
        return summary
    except Exception as e:
        logger.error(f"Error summarizing with model: {e}")
        return "Error summarizing articles."

# Process articles
def process_articles(news_articles):
    min_score = -0.3
    max_score = 0.45
    headlines = [article['Summary'] for article in news_articles[:10] if 'Summary' in article]
    if not headlines:
        logger.error("No headlines found in the articles.")
        return {"summary": "No articles available.", "overall_score": 0, "investment_advice": "No data."}

    combined_summary = summarize_articles_together(headlines)
    sentiment_scores = [get_sentiment_score(headline) for headline in headlines]
    avg_sentiment_score = (math.floor((sum(sentiment_scores) / len(sentiment_scores)) * 100) / 100) if sentiment_scores else 0.00
    normalized_score = normalize_sentiment_score(avg_sentiment_score, min_score, max_score)
    normalized_score = Decimal(normalized_score).quantize(Decimal('0.00'), rounding=ROUND_DOWN)
    
    if normalized_score >= 7:
        investment_advice = "Strong positive sentiment. Potential investment opportunity, subject to further analysis."
    elif normalized_score >= 4:
        investment_advice = "Moderate sentiment. Conduct in-depth research and risk assessment before investing."
    else:
        investment_advice = "Weak sentiment. Consider avoiding or reassessing investment strategy."

    
    return {
        "summary": combined_summary,
        "overall_score": normalized_score,
        "investment_advice": investment_advice
    }

@app.post("/analyze_company/")
async def analyze_company(data: CompanyRequest):
    company_name = data.company_name
    try:
        news_articles = fetch_and_return_articles(company_name)
        if not news_articles:
            logger.error(f"No articles found for company: {company_name}")
            raise HTTPException(status_code=404, detail="No articles found for this company.")
        
        result = process_articles(news_articles)
        return result
    except Exception as e:
        logger.error(f"Error in analyze_company: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error. Please try again later.")
