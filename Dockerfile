# Python का लेटेस्ट लाइटवेट वर्जन इस्तेमाल करें
FROM python:3.10-slim

# वर्किंग डायरेक्टरी सेट करें
WORKDIR /app

# सिस्टम डिपेंडेंसीज इंस्टॉल करें
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# रिक्वायरमेंट फाइल कॉपी करें और इंस्टॉल करें
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# बाकी सारा कोड कॉपी करें
COPY . .

# Flask के लिए पोर्ट एक्सपोज करें
EXPOSE 8080

# बॉट को चलाने की कमांड (Gunicorn Flask को हैंडल करेगा और Python बॉट को)
CMD gunicorn --bind 0.0.0.0:8080 bot:app & python3 bot.py
