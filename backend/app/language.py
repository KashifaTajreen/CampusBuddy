"""Bounded campus vocabulary, not a general-purpose translation engine."""
import re
import unicodedata

WORDS = {
    "kn": {
        "ಇಂದು": "today", "ಇವತ್ತು": "today", "ನಾಳೆ": "tomorrow", "ಮುಂದಿನ": "next",
        "ಈಗ": "current", "ತರಗತಿಗಳು": "classes", "ತರಗತಿ": "class", "ವೇಳಾಪಟ್ಟಿ": "timetable",
        "ಕಚೇರಿ": "office", "ಯಾರು": "who", "ಬೋಧಿಸುತ್ತಾರೆ": "teaches", "ಎಲ್ಲಿ": "where",
        "ಕ್ಯಾಂಟೀನ್": "canteen", "ಊಟ": "food", "ಸಸ್ಯಾಹಾರಿ": "vegetarian", "ರೂಪಾಯಿ": "rupees",
        "ಒಳಗೆ": "under", "ಮತ್ತು": "and", "ಕಾರ್ಯಕ್ರಮಗಳು": "events", "ಕಾರ್ಯಕ್ರಮ": "events",
        "ಕಾರ್ಯಾಗಾರ": "workshop", "ಕಾರ್ಯಾಗಾರಗಳು": "workshop", "ಸೆಮಿನಾರ್": "seminar", "ಸ್ಪರ್ಧೆ": "event", "ಸೂಚನೆ": "notice",
        "ಗ್ರಂಥಾಲಯ": "library", "ನೀತಿ": "policy", "ಪರೀಕ್ಷೆ": "exam", "ಅವರ": "their",
        "ನನ್ನ": "my", "ivattu": "today", "naale": "tomorrow", "mundina": "next",
        "nanna": "my", "taragati": "class", "yelli": "where", "oota": "food",
    },
    "hi": {
        "बीता कल": "yesterday", "आज": "today", "कल": "tomorrow", "अभी": "current",
        "अगली": "next", "कक्षाएं": "classes", "कक्षा": "class", "समयसारणी": "timetable",
        "कार्यालय": "office", "कौन": "who", "पढ़ाते": "teaches", "कहाँ": "where",
        "खाना": "food", "शाकाहारी": "vegetarian", "मेनू": "menu", "कार्यक्रम": "events",
        "कार्यशाला": "workshop", "सेमिनार": "seminar", "प्रतियोगिता": "event",
        "सूचना": "notice", "पुस्तकालय": "library", "नीति": "policy", "परीक्षा": "exam",
        "और": "and", "उनकी": "their", "मेरी": "my", "मेरा": "my", "aaj": "today",
        "kal": "tomorrow", "agli": "next", "mera": "my", "meri": "my", "kaun": "who",
        "kahan": "where", "khana": "food", "shakahari": "vegetarian",
    },
}

WORDS["kn"].update({"ರಜೆ": "holiday", "ಶೈಕ್ಷಣಿಕ ಕ್ಯಾಲೆಂಡರ್": "academic calendar", "ಪರೀಕ್ಷೆ": "exam"})
WORDS["hi"].update({"छुट्टी": "holiday", "अवकाश": "holiday", "शैक्षणिक कैलेंडर": "academic calendar"})
WORDS["kn"].update({"yavaga":"when", "yavattu":"when"})
WORDS["hi"].update({"hai":"", "kya":"", "hoga":"will"})


def normalize(question, preference="auto"):
    detected = "kn" if re.search(r"[\u0c80-\u0cff]", question) else "hi" if re.search(r"[\u0900-\u097f]", question) else "en"
    q = question.lower().replace("’", "'")
    if re.search(r"\bkal\b|कल",q) and "बीता कल" not in q:
        direction = ("yesterday" if re.search(r"\b(?:was|were|did|had)\b|था|थी|थे|बीता",q) else
                     "tomorrow" if re.search(r"\b(?:will|am i|do i|can i|is there|hai|hoga|tomorrow)\b|है|होगा",q)
                     else "ambiguous_relative_date")
        q=re.sub(r"\bkal\b|कल",direction,q)
    aliases={"kn":0,"hi":0}
    for language, lexicon in WORDS.items():
        for word in sorted(lexicon, key=len, reverse=True):
            pattern = re.escape(word) if not word.isascii() else r"\b" + re.escape(word) + r"\b"
            if re.search(pattern, q):
                if word.isascii():
                    aliases[language]+=1
                q = re.sub(pattern, " " + lexicon[word] + " ", q)
    if detected=="en" and max(aliases.values())>=2:
        detected=max(aliases,key=aliases.get)
    q = "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in q)
    for alias, subject in {"os": "Operating Systems", "ml": "Machine Learning", "dbms": "Database"}.items():
        if re.search(r"\b" + alias + r"\b", q) and "who" in q and "teaches" in q:
            q = "who teaches " + subject + " to my section"
            break
    q = re.sub(r"(\d+(?:\.\d+)?)\s+(?:rupees\s+)?under\b", r"under \1", q)
    q = re.sub(r"who is teaching", "who teaches", q)
    return " ".join(q.split()), detected if preference == "auto" else preference


def localize(result, language):
    if language == "en":
        return result
    labels = {
        "kn": {"classes": "ನಿಮ್ಮ ತರಗತಿಗಳು", "faculty": "ಅಧ್ಯಾಪಕರ ವಿವರಗಳು", "food": "ಲಭ್ಯವಿರುವ ಆಹಾರ", "meals": "ಆಹಾರದ ಆಯ್ಕೆಗಳು", "events": "ಕಾರ್ಯಕ್ರಮಗಳು", "passages": "ಮೂಲ ದಾಖಲೆಯ ಸಾಕ್ಷ್ಯ", "text": "ಈ ಪ್ರಶ್ನೆಗೆ ಬೆಂಬಲಿಸುವ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ"},
        "hi": {"classes": "आपकी कक्षाएं", "faculty": "शिक्षक की जानकारी", "food": "उपलब्ध भोजन", "meals": "भोजन के विकल्प", "events": "कार्यक्रम", "passages": "मूल दस्तावेज़ से प्रमाण", "text": "इस प्रश्न के लिए सहायक जानकारी उपलब्ध नहीं है"},
    }
    table = labels[language]
    if result.get("answer_mode") != "generated":
        label = table.get(result["kind"], table["passages"])
        missing = "कोई मिलती जानकारी नहीं मिली।" if language == "hi" else "ಹೊಂದುವ ಮಾಹಿತಿ ಸಿಗಲಿಲ್ಲ."
        evidence_note = "प्रमाण मूल भाषा में हैं।" if language == "hi" else "ಸಾಕ್ಷ್ಯ ಮೂಲ ಭಾಷೆಯಲ್ಲಿದೆ."
        translations={
            "ambiguous_date":{"hi":"‘कल’ से आपका मतलब बीता हुआ दिन है या आने वाला दिन? कृपया तारीख बताएं।", "kn":"‘ಕಲ್’ ಎಂದರೆ ನಿನ್ನೆಯೇ ಅಥವಾ ನಾಳೆಯೇ? ದಯವಿಟ್ಟು ದಿನಾಂಕ ತಿಳಿಸಿ."},
            "schedule_unknown":{"hi":"उस तारीख के लिए प्रकाशित समयसारणी उपलब्ध नहीं है; मैं खाली समय की पुष्टि नहीं कर सकता।", "kn":"ಆ ದಿನದ ಪ್ರಕಟಿತ ವೇಳಾಪಟ್ಟಿ ಲಭ್ಯವಿಲ್ಲ; ನೀವು ಬಿಡುವಾಗಿದ್ದೀರಿ ಎಂದು ಖಚಿತಪಡಿಸಲಾಗುವುದಿಲ್ಲ."},
            "busy":{"hi":"उस समय आपकी प्रकाशित कक्षा या परीक्षा दर्ज है।", "kn":"ಆ ಸಮಯದಲ್ಲಿ ನಿಮ್ಮ ಪ್ರಕಟಿತ ತರಗತಿ ಅಥವಾ ಪರೀಕ್ಷೆ ಇದೆ."},
            "no_recorded_conflict":{"hi":"प्रकाशित समयसारणी में उस समय कक्षा या परीक्षा नहीं मिली। अन्य निजी कार्यक्रमों की पुष्टि करें।", "kn":"ಪ್ರಕಟಿತ ವೇಳಾಪಟ್ಟಿಯಲ್ಲಿ ಆ ಸಮಯಕ್ಕೆ ತರಗತಿ ಅಥವಾ ಪರೀಕ್ಷೆ ಕಂಡುಬಂದಿಲ್ಲ. ಇತರ ವೈಯಕ್ತಿಕ ಕಾರ್ಯಕ್ರಮಗಳನ್ನು ಪರಿಶೀಲಿಸಿ."},
        }
        translated=translations.get(result.get("reason"),{}).get(language)
        result["answer"] = translated or (label+". "+result["answer"]+" "+(evidence_note if result["records"] else missing))
    result["followups"] = []
    return result
