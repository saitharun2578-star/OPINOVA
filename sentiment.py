"""
Opinova — Lightweight Rule-Based Sentiment & Keyword Analyzer
Performs localized lexical keyword extraction and sentiment classification without external AI APIs.
"""

import re
from collections import Counter

# Standard English stopwords to filter out for keyword clouds
STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both",
    "but", "by", "can", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does",
    "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "had",
    "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd",
    "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself",
    "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off",
    "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over",
    "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so",
    "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't",
    "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when",
    "when's", "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's",
    "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your",
    "yours", "yourself", "yourselves", "also", "really", "much", "well", "like", "get", "make",
    "just", "class", "course", "subject", "student", "students", "lecture", "lectures"
}

POSITIVE_LEXICON = {
    "clear", "helpful", "great", "excellent", "engaging", "interactive", "supportive", "approachable",
    "practical", "thorough", "organized", "responsive", "knowledgeable", "inspiring", "energetic",
    "patient", "structured", "enjoyable", "informative", "friendly", "encouraging", "detailed",
    "constructive", "insightful", "brilliant", "fantastic", "accessible", "positive", "timely"
}

NEGATIVE_LEXICON = {
    "confusing", "fast", "rushed", "slow", "hard", "difficult", "heavy", "disorganized", "boring",
    "unclear", "quiet", "inaudible", "echo", "late", "delayed", "stressful", "overwhelming",
    "harsh", "tough", "monotone", "vague", "complicated", "disconnected", "unprepared", "chaotic"
}

THEME_PATTERNS = {
    "Clarity & Explanations": ["clarity", "clear", "explaining", "explanation", "understand", "whiteboard", "examples"],
    "Pacing & Speed": ["pacing", "pace", "fast", "rush", "rushed", "slow", "speed", "quick"],
    "Course Materials & Slides": ["slides", "materials", "notes", "readings", "recordings", "syllabus", "textbook"],
    "Engagement & Interaction": ["engaging", "engagement", "interactive", "discussion", "questions", "monologue", "boring"],
    "Support & Office Hours": ["supportive", "helpful", "approachable", "office hours", "responsive", "doubt", "help"],
    "Assignments & Workload": ["assignment", "homework", "workload", "deadline", "project", "quiz", "exam", "grading"],
    "Classroom & Environment": ["classroom", "audio", "mic", "microphone", "echo", "volume", "projector", "room"]
}

def clean_and_tokenize(text):
    """Normalizes text into clean lowercase alpha words."""
    if not text:
        return []
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return [w for w in words if w not in STOPWORDS]

def analyze_sentiment_and_themes(feedback_records):
    """
    Analyzes all text comments across feedback submissions.
    Returns:
    - theme_summary: occurrences of core academic themes
    - top_positive_keywords: top frequent positive terms
    - top_improvement_keywords: top frequent improvement terms
    - sentiment_breakdown: positive, neutral, negative counts
    - key_findings: human-readable summaries of positive highlights and improvement needs
    """
    if not feedback_records:
        return {
            "has_data": False,
            "sentiment_counts": {"Positive": 0, "Neutral": 0, "Negative": 0},
            "top_positive_keywords": [],
            "top_improvement_keywords": [],
            "theme_frequencies": {},
            "key_strengths": [],
            "key_improvements": [],
            "disclaimer": "Rule-based keyword insight: Sentiment classifications and theme extractions are estimated using lexical matching and student ratings for rapid operational guidance."
        }

    positive_tokens = []
    improvement_tokens = []
    theme_counts = Counter()

    pos_responses = 0
    neu_responses = 0
    neg_responses = 0

    for r in feedback_records:
        overall_rating = r.get("rating_overall", 3)
        ww = r.get("comment_working_well") or ""
        imp = r.get("comment_improvements") or ""
        sug = r.get("comment_suggestions") or ""

        # Tokenize
        ww_words = clean_and_tokenize(ww)
        imp_words = clean_and_tokenize(imp)
        sug_words = clean_and_tokenize(sug)

        positive_tokens.extend(ww_words)
        improvement_tokens.extend(imp_words)
        improvement_tokens.extend(sug_words)

        all_text = f"{ww} {imp} {sug}".lower()

        # Map to themes
        for theme_name, keywords in THEME_PATTERNS.items():
            if any(k in all_text for k in keywords):
                theme_counts[theme_name] += 1

        # Classify comment sentiment
        pos_score = sum(1 for w in ww_words if w in POSITIVE_LEXICON) + (2 if overall_rating >= 4 else 0)
        neg_score = sum(1 for w in imp_words if w in NEGATIVE_LEXICON) + (2 if overall_rating <= 2 else 0)

        if pos_score > neg_score and overall_rating >= 4:
            pos_responses += 1
        elif neg_score > pos_score and overall_rating <= 2:
            neg_responses += 1
        elif overall_rating == 3 or pos_score == neg_score:
            neu_responses += 1
        elif overall_rating >= 4:
            pos_responses += 1
        else:
            neg_responses += 1

    # Extract top keywords
    pos_freq = Counter(positive_tokens).most_common(8)
    imp_freq = Counter(improvement_tokens).most_common(8)

    # Format findings
    key_strengths = []
    if pos_freq:
        top_words = [word for word, _ in pos_freq[:4]]
        key_strengths.append(f"Frequent positive mentions emphasize: {', '.join(top_words)}.")
    if theme_counts.get("Clarity & Explanations", 0) > 0:
        key_strengths.append("High student recognition for structured explanations and conceptual clarity.")
    if theme_counts.get("Support & Office Hours", 0) > 0:
        key_strengths.append("Positive feedback regarding instructor approachability and assistance.")

    key_improvements = []
    if imp_freq:
        top_imp_words = [word for word, _ in imp_freq[:4]]
        key_improvements.append(f"Student comments frequently highlight areas regarding: {', '.join(top_imp_words)}.")
    if theme_counts.get("Pacing & Speed", 0) > 0:
        key_improvements.append("Pacing feedback noted: Consider pacing adjustments or early slide releases.")
    if theme_counts.get("Course Materials & Slides", 0) > 0:
        key_improvements.append("Request for timely slide and supplementary lecture material uploads.")
    if theme_counts.get("Classroom & Environment", 0) > 0:
        key_improvements.append("Physical/acoustic classroom check recommended (mic volume, room echo).")

    return {
        "has_data": True,
        "sentiment_counts": {
            "Positive": pos_responses,
            "Neutral": neu_responses,
            "Negative": neg_responses
        },
        "top_positive_keywords": [{"keyword": k, "count": c} for k, c in pos_freq],
        "top_improvement_keywords": [{"keyword": k, "count": c} for k, c in imp_freq],
        "theme_frequencies": dict(theme_counts),
        "key_strengths": key_strengths,
        "key_improvements": key_improvements,
        "disclaimer": "Rule-based keyword insight: Sentiment classifications and theme extractions are estimated using lexical matching and student ratings for rapid operational guidance."
    }
