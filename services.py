import os
import json
import base64
import datetime
import random
import difflib
import time
import io
import tempfile
import re
from collections import defaultdict
from pydub import AudioSegment
import ToJyutping
from google import genai
from google.genai import types

# ==================== AZURE Speech Service ====================
import azure.cognitiveservices.speech as speechsdk
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential

# Load API Keys
API_KEY = os.environ.get("GEMINI_API_KEY")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastasia")
AZURE_TEXT_KEY = os.environ.get("AZURE_TEXT_KEY")
AZURE_TEXT_ENDPOINT = os.environ.get("AZURE_TEXT_ENDPOINT")

# Initialize clients
client = genai.Client(api_key=API_KEY)

# Initialize Azure clients (lazy loading)
azure_text_client = None

def get_speech_config():
    
    if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        try:
           
            clean_key = AZURE_SPEECH_KEY.strip().replace('"', '').replace("'", "")
            clean_region = AZURE_SPEECH_REGION.strip().replace('"', '').replace("'", "")
            
            config = speechsdk.SpeechConfig(
                subscription=clean_key,
                region=clean_region
            )
            config.speech_synthesis_voice_name = "zh-HK-HiuGaaiNeural"
            config.speech_recognition_language = "zh-HK"
            return config
        except Exception as e:
            print(f"Azure Speech Service failed: {str(e)}")
            return None
    return None

def get_text_analytics_client():
    global azure_text_client
    if not azure_text_client and AZURE_TEXT_KEY and AZURE_TEXT_ENDPOINT:
        try:
            credential = AzureKeyCredential(AZURE_TEXT_KEY)
            azure_text_client = TextAnalyticsClient(
                endpoint=AZURE_TEXT_ENDPOINT,
                credential=credential
            )
        except Exception as e:
            print(f"Azure Text Analytics configuration failed: {str(e)}")
            azure_text_client = None
    return azure_text_client

user_progress = defaultdict(list)

# ==================== Exercise Dataset (Mike's full version) ====================
PRONUNCIATION_EXERCISES = {
    "beginner": {
        "common_words": [
            {"word": "你好", "ipa": "nei5 hou2", "category": "greetings", "description": "基本問候語", "meaning": "Hello", "image": "你好.png"},
            {"word": "多謝", "ipa": "do1 ze6", "category": "politeness", "description": "表達感謝", "meaning": "Thank you", "image": "多謝.jpeg"},
            {"word": "早晨", "ipa": "zou2 san4", "category": "greetings", "description": "早上問候", "meaning": "Good morning", "image": "早晨.jpeg"},
            {"word": "再見", "ipa": "zoi3 gin3", "category": "greetings", "description": "道別", "meaning": "Goodbye", "image": "再見.jpeg"},
            {"word": "對唔住", "ipa": "deoi3 m4 zyu6", "category": "politeness", "description": "道歉", "meaning": "Sorry", "image": "對唔住.jpeg"}
        ],
    },
    "intermediate": {
        "minimal_pairs": [
            {"pair": ["年", "連"], "ipa": ["nin4", "lin4"], "focus": "n/l 區分", "category": "minimal_pairs", "meaning": "Year vs. Connect"},
            {"pair": ["廣", "講"], "ipa": ["gwong2", "gong2"], "focus": "gw/g 區分", "category": "minimal_pairs", "meaning": "Wide vs. Speak"},
            {"pair": ["我", "哦"], "ipa": ["ngo5", "o5"], "focus": "ng 聲母", "category": "minimal_pairs", "meaning": "I/Me vs. Oh"},
            {"pair": ["三", "山"], "ipa": ["saam1", "saan1"], "focus": "aa/a 區分", "category": "minimal_pairs", "meaning": "Three vs. Mountain"}
        ],
        "tongue_twisters": [
            {"text": "入實驗室撳緊急掣", "ipa": "jap6 sat6 jim6 sat1 gam6 gan2 gap1 zai3",
             "difficulty": "medium", "category": "tongue_twisters", "description": "經典繞口令", "meaning": "Enter the lab and press the emergency button"},
            {"text": "床腳撞牆角，牆角撞床腳", "ipa": "cong4 goek3 zong6 coeng4 gok3, coeng4 gok3 zong6 cong4 goek3",
             "difficulty": "hard", "category": "tongue_twisters", "description": "聲母練習", "meaning": "Bed leg hits wall corner, wall corner hits bed leg"}
        ],
        "sentences": [
            {"text": "今日天氣好好", "ipa": "gam1 jat6 tin1 hei3 hou2 hou2", "category": "sentences", "focus": "重複音節", "meaning": "The weather is great today"},
            {"text": "我想食蘋果", "ipa": "ngo5 soeng2 sik6 ping4 gwo2", "category": "sentences", "focus": "日常用語", "meaning": "I want to eat an apple"}
        ]
    },
    "advanced": {
        "challenging_words": [
            {"word": "齷齪", "ipa": "ak1 cuk1", "category": "difficult", "description": "難讀詞語", "meaning": "Dirty / Filthy"},
            {"word": "尷尬", "ipa": "gaam3 gaai3", "category": "difficult", "description": "容易讀錯", "meaning": "Awkward / Embarrassing"},
            {"word": "籮筐", "ipa": "lo4 hong1", "category": "difficult", "description": "聲調組合", "meaning": "Basket"}
        ],
        "long_sentences": [
            {"text": "香港係一個國際化嘅大都市，有好多唔同文化嘅人一齊生活",
             "ipa": "hoeng1 gong2 hai6 jat1 go3 gwok3 zai3 faa3 ge3 daai6 dou1 si5, jau5 hou2 do1 m4 tung4 man4 faa3 ge3 jan4 jat1 cai4 sang1 wut6",
             "category": "sentences", "focus": "長句流暢度", "meaning": "Hong Kong is an international city where people of many cultures live together"},
            {"text": "噚日我去咗超級市場買餸，見到好多新鮮嘅蔬菜同生果",
             "ipa": "cam4 jat6 ngo5 heoi3 zo2 ciu1 kap1 si5 coeng4 maai5 sung3, gin3 dou3 hou2 do1 san1 sin1 ge3 so1 coi3 tung4 saang1 gwo2",
             "category": "sentences", "focus": "敘述能力", "meaning": "Yesterday I went to the supermarket and saw lots of fresh vegetables and fruits"}
        ]
    }
}

# ==================== Word Meaning Dictionary ====================
WORD_MEANINGS = {
    "你好": "Hello",
    "多謝": "Thank you",
    "早晨": "Good morning",
    "再見": "Goodbye",
    "對唔住": "Sorry",
    "唔該": "Excuse me / Thank you",
    "蘋果": "Apple",
    "醫生": "Doctor",
    "爸爸": "Dad",
    "媽媽": "Mom",
    "哥哥": "Older brother",
    "姐姐": "Older sister",
    "弟弟": "Younger brother",
    "妹妹": "Younger sister",
    "老師": "Teacher",
    "同學": "Classmate",
    "朋友": "Friend",
    "學校": "School",
    "返工": "Go to work",
    "返學": "Go to school",
    "食飯": "Eat",
    "飲水": "Drink water",
    "瞓覺": "Sleep",
    "起身": "Wake up",
    "行路": "Walk",
    "跑步": "Run",
    "開心": "Happy",
    "傷心": "Sad",
    "多謝你": "Thank you",
    "好嘢": "Great",
    "係": "Yes / Is",
    "唔係": "No / Is not",
    "幾多": "How many",
    "邊度": "Where",
    "點解": "Why",
    "乜嘢": "What",
    "幾時": "When",
    "水": "Water",
    "牛奶": "Milk",
    "麵包": "Bread",
    "雞蛋": "Egg",
    "西瓜": "Watermelon",
    "香蕉": "Banana",
    "橙": "Orange",
    "提子": "Grape",
    "車": "Car",
    "巴士": "Bus",
    "地鐵": "MTR / Subway",
    "飛機": "Airplane",
    "公園": "Park",
    "超級市場": "Supermarket",
    "醫院": "Hospital",
    "圖書館": "Library",
    "動物園": "Zoo",
    "遊樂場": "Playground",
    "貓": "Cat",
    "狗": "Dog",
    "魚": "Fish",
    "鳥": "Bird",
    "兔": "Rabbit",
    "紅色": "Red",
    "藍色": "Blue",
    "綠色": "Green",
    "黃色": "Yellow",
    "白色": "White",
    "黑色": "Black",
    "一": "One",
    "二": "Two",
    "三": "Three",
    "四": "Four",
    "五": "Five",
    "六": "Six",
    "七": "Seven",
    "八": "Eight",
    "九": "Nine",
    "十": "Ten",
    "大": "Big",
    "細": "Small",
    "多": "Many",
    "少": "Few",
    "好": "Good",
    "靚": "Pretty",
    "快": "Fast",
    "慢": "Slow",
    "凍": "Cold",
    "熱": "Hot",
    "齷齪": "Dirty / Filthy",
    "尷尬": "Awkward / Embarrassing",
    "籮筐": "Basket",
    "年": "Year",
    "連": "Connect",
    "廣": "Wide",
    "講": "Speak",
    "我": "I / Me",
    "哦": "Oh",
    "山": "Mountain",
}


def lookup_meaning(text):
    """Look up English meaning for a Chinese word/sentence."""
    if not text:
        return ""
    # Direct match
    if text in WORD_MEANINGS:
        return WORD_MEANINGS[text]
    # Try removing punctuation
    clean = text.rstrip("。，！？、")
    if clean in WORD_MEANINGS:
        return WORD_MEANINGS[clean]
    # For sentences or unknown words, use Gemini to translate
    if API_KEY and client:
        try:
            resp = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"Translate this Cantonese text to English in one short sentence. Only reply with the translation, nothing else: {text}"
            )
            meaning = resp.text.strip().strip('"').strip("'")
            if meaning:
                return meaning
        except:
            pass
    # Fallback: partial word matching
    parts = []
    for word in WORD_MEANINGS:
        if word in clean and len(word) >= 2:
            parts.append(f"{word}={WORD_MEANINGS[word]}")
    if parts:
        return " | ".join(parts[:4])
    return ""


# ==================== Utility Functions ====================

def blob_to_base64(file_storage):
    file_content = file_storage.read()
    file_storage.seek(0)
    return base64.b64encode(file_content).decode('utf-8')

def _parse_gemini_json(text):
    """Safely parse Gemini JSON response."""
    try:
        clean_text = text.replace('```json', '').replace('```', '').strip()
        data = json.loads(clean_text)
        if isinstance(data, list):
            return data[0] if len(data) > 0 else {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        print(f"JSON Parse Error: {e} | Text: {text}")
        return {}

# ==================== Core: Azure + Gemini Fallback (Robust Phonetic Version) ====================

def analyze_pronunciation_azure(audio_file, reference_text):
   
    speech_config = get_speech_config()
    if not speech_config:
        print("azure config error, use Gemini")
        return process_audio(audio_file, target_word=reference_text)

    try:
        print(f"'{reference_text}'")
        audio_file.seek(0)
        audio_bytes = audio_file.read()
        
        try:
            
            audio_stream = io.BytesIO(audio_bytes)
            audio_segment = AudioSegment.from_file(audio_stream)
            audio_segment = audio_segment.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            audio_segment.export(tmp.name, format="wav")
            tmp_path = tmp.name
        except Exception as conv_err:
            print(f"Format conversion failed: {conv_err}")
            audio_file.seek(0)
            return process_audio(audio_file, target_word=reference_text)

        try:
            
            audio_config_stt = speechsdk.audio.AudioConfig(filename=tmp_path)
            recognizer_stt = speechsdk.SpeechRecognizer(
                speech_config=speech_config, 
                audio_config=audio_config_stt
            )
            
            result_stt = recognizer_stt.recognize_once_async().get()
            
            if result_stt.reason == speechsdk.ResultReason.RecognizedSpeech:
                actual_heard_text = result_stt.text
                
                
                clean_recognized = re.sub(r'[^\w\s\u4e00-\u9fff]', '', actual_heard_text)
                clean_target = re.sub(r'[^\w\s\u4e00-\u9fff]', '', reference_text)
                
              
                target_jyutping = ToJyutping.get_jyutping_text(clean_target)
                heard_jyutping = ToJyutping.get_jyutping_text(clean_recognized)
                
                
                target_sounds = target_jyutping.split()
                heard_sounds = heard_jyutping.split()
                
               
                similarity = difflib.SequenceMatcher(None, heard_sounds, target_sounds).ratio()
                acoustic_score = int(similarity * 100)

                print(f" target (Jyutping): {target_jyutping}")
                print(f"actually (Jyutping): {heard_jyutping}")
                print(f"phonetic: {acoustic_score}")

                
                feedback = ""
                if acoustic_score >= 80: 
                    feedback = "發音標準！👏"
                elif acoustic_score >= 50: 
                    feedback = f"讀得唔錯，但我聽到嘅聲調似係「{actual_heard_text}」。"
                else: 
                    feedback = f"加油！你讀咗類似「{actual_heard_text}」嘅音，試下再讀清楚啲。"

                return {
                    "transcript": actual_heard_text, 
                    "score": acoustic_score,         
                    "accuracy_percent": acoustic_score, 
                    "humanPerception": feedback,
                    "jyutping": heard_jyutping,      
                    "engine": "Azure Phonetic"
                }
                
            elif result_stt.reason == speechsdk.ResultReason.NoMatch:
                print("⚠️ Azure cannot recognize speech,switch Gemini")
                audio_file.seek(0)
                return process_audio(audio_file, target_word=reference_text)
                
            elif result_stt.reason == speechsdk.ResultReason.Canceled:
                print("⚠️ Azure cancelled，use Gemini")
                audio_file.seek(0)
                return process_audio(audio_file, target_word=reference_text)

        finally:
            
            if os.path.exists(tmp_path):
                try:
                    if 'recognizer_stt' in locals(): del recognizer_stt
                    if 'audio_config_stt' in locals(): del audio_config_stt
                    os.remove(tmp_path)
                except:
                    pass
                
    except Exception as e:
        print(f"❌ Azure Exception: {e} -> use Gemini")
        audio_file.seek(0)
        return process_audio(audio_file, target_word=reference_text)

    return {"error": "Unknown error"}

# ==================== Gemini Logic ====================

def process_audio(audio_file, target_word=None, target_ipa=None):
    """Gemini processing logic (Fallback / Phase 2)"""
    if not API_KEY:
        return {"error": "API Key missing"}

    try:
        base64_audio = blob_to_base64(audio_file)

        if target_word:
            instruction = f"目標詞語：{target_word}。請評分 (0-100) 並提供粵拼。"
        else:
            instruction = "用戶正在進行自由對話。請識別內容並轉換為文字及粵拼。"

        prompt = f"""
        你是一位香港粵語言語治療師。
        任務：{instruction}
        注意：評語 (humanPerception) 必須使用**純廣東話 (繁體中文)**，**嚴禁包含任何英文**翻譯或單字。
        Output JSON:
        {{
            "transcript": "文字",
            "jyutping": "粵拼",
            "score": 0-100,
            "humanPerception": "簡短評語 (純廣東話)"
        }}
        """

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(data=base64.b64decode(base64_audio), mime_type="audio/wav"),
                types.Part.from_text(text=prompt)
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        result = _parse_gemini_json(response.text)

        if "accuracy_percent" not in result:
            result["accuracy_percent"] = result.get("score", 0)
        result["engine"] = "Gemini"
        if "transcript" not in result:
            result["transcript"] = "無法識別"

        return result

    except Exception as e:
        print(f"Gemini Error: {e}")
        return {"error": str(e)}

# ==================== Phase 2 Logic (Mission Chat) ====================

def process_mission_chat(audio_file, topic, current_mission_words, history):
    if not API_KEY:
        return {"error": "API Key missing"}
    try:
        base64_audio = blob_to_base64(audio_file)

        # 1. Transcribe
        trans_resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(data=base64.b64decode(base64_audio), mime_type="audio/wav"),
                types.Part.from_text(text="轉寫廣東話 JSON: {transcript: string}")
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        trans_result = _parse_gemini_json(trans_resp.text)
        user_text = trans_result.get("transcript", "")

        
        mission_str = ','.join(current_mission_words)

        logic_prompt = f"""
        你是一位親切的兒童言語治療師。

        情境：{topic}
        用戶說："{user_text}"
        上一輪任務詞：[{mission_str}]
        對話歷史：{history}

        請嚴格按照以下工作流程生成回應：
        1. 檢查用戶剛才的回答有沒有使用上一輪任務詞 (Semantic Match)。
        2. 根據目前情境，**先想好下一輪的 2 到 3 個任務詞** (next_mission_words，必須是純中文名詞或動詞)。
        3. **然後，根據你選定的任務詞，設計一個廣東話問題** (bot_reply)。這個問題必須能夠自然地引導用戶在回答時，講出你剛剛設定的 next_mission_words。
           (例如：如果 next_mission_words 是 ["蘋果", "橙"]，你的 bot_reply 應該是類似 "你想食蘋果定係橙呀？")
        4. 回應必須使用親切的廣東話 (繁體中文)，絕對不能包含任何英文翻譯或單字。

        Output JSON:
        {{
            "mission_success": bool,
            "next_mission_words": ["詞1", "詞2"],
            "bot_reply": "string "
        }}
        """

        logic_resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=logic_prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        res = _parse_gemini_json(logic_resp.text)

        return {
            "transcript": user_text,
            "bot_reply": res.get("bot_reply", "做得好！"),
            "next_mission_words": res.get("next_mission_words", ["蘋果", "橙"]),
            "mission_success": res.get("mission_success", False),
            "score": 80 if res.get("mission_success", False) else 40
        }
    except Exception as e:
        print(f"Mission Chat Error: {e}")
        return {"error": str(e)}

# ==================== Assessment Content Generation ====================

def generate_assessment_content():
    """Generate initial assessment content."""
    try:
        prompt = """
        生成一個粵語發音評估的 JSON 內容。
        對象：需要接受言語治療的兒童/成人。
        語言要求：句子和對話內容使用繁體中文 (廣東話)，但每個項目必須包含英文翻譯。

        格式：
        1. "readingTasks": 6個由淺入深的簡單句子 (text, jyutping, meaning)
           - "meaning": 該句子的英文翻譯，例如 "Hello."
        2. "topics": 3個生活化對話場景 (id, name, startWords, intro)
           - 重點工作流程：先決定場景 (name)，再決定任務詞 (startWords)，最後根據任務詞設計開場白 (intro)。
           - "startWords": 2至3個相關的名詞 (純中文)，例如 ["滑梯", "鞦韆"]
           - "intro": 開場白必須是中文問題，而且**必須巧妙地引導用戶回答時使用上述的 startWords**。
             例如如果任務詞是 ["蘋果", "橙"]，intro 必須是 "媽媽去超級市場買生果，你想買蘋果定係買橙呀？"
        """

        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return _parse_gemini_json(resp.text)
    except:
        print("Failed to generate assessment content.")
        return {
            "readingTasks": [
                {"text": "你好。", "jyutping": "nei5 hou2", "meaning": "Hello."},
                {"text": "早晨。", "jyutping": "zou2 san4", "meaning": "Good morning."},
                {"text": "我叫陳大文。", "jyutping": "ngo5 giu3 can4 daai6 man4", "meaning": "My name is Chan Tai Man."},
                {"text": "今日天氣好好。", "jyutping": "gam1 jat6 tin1 hei3 hou2 hou2", "meaning": "The weather is great today."},
                {"text": "我想食一個紅蘋果。", "jyutping": "ngo5 soeng2 sik6 jat1 go3 hung4 ping4 gwo2", "meaning": "I want to eat a red apple."},
                {"text": "哥哥高過嗰個個子高嘅哥哥。", "jyutping": "go1 go1 gou1 gwo3 go2 go3 go3 zi2 gou1 ge3 go1 go1", "meaning": "Big brother is taller than that tall big brother."}
            ],
            "topics": [
                {"id": "park", "name": "遊樂場", "startWords": ["滑梯", "鞦韆", "朋友"], "intro": "今日去遊樂場，你想同朋友一齊玩滑梯定係鞦韆先呀？"},
                {"id": "food", "name": "超級市場", "startWords": ["蘋果", "橙", "香蕉"], "intro": "媽媽今晚去超級市場買生果，你想食蘋果、橙定係香蕉呀？"}
            ]
        }

# ==================== Report Generation (Pong's version - best) ====================

def generate_analysis_report(reading_data, conv_data):
    """Generate dual reports: client_report and professional_report."""
    if not API_KEY:
        return {"client_report": "Error: API Key missing", "professional_report": "Error: API Key missing"}

    prompt = f"""
    你是一位專業的香港言語治療師。請根據用戶的評估數據生成兩份廣東話/繁體中文報告：

    1. "client_report" (簡易版報告)：專門給家長和學生看。用詞淺白親切，不要太多專有名詞，總結表現並給予多啲鼓勵，列出可以進步的地方。
    2. "professional_report" (專業版報告)：專門給治療師內部查看。必須包含詳細的語音學分析、IPA拼音對比分析、錯音記錄、以及精確的治療干預建議。

    Reading Data: {json.dumps(reading_data)}
    Conversation Data: {json.dumps(conv_data)}

    請以 JSON 格式回傳：
    {{
        "client_report": "markdown 格式內容...",
        "professional_report": "markdown 格式內容..."
    }}
    """
    try:
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return _parse_gemini_json(resp.text)
    except Exception as e:
        return {"client_report": f"generate_analysis_report failed: {str(e)}", "professional_report": "generate_analysis_report failed"}


def generate_growth_report(practice_history):
    """Generate progress analysis report comparing old and new data."""
    if not API_KEY:
        return "Error: API Key missing"

    if len(practice_history) > 15:
        data_subset = practice_history[-10:] + practice_history[:5]
    else:
        data_subset = practice_history

    data_str = json.dumps(data_subset, ensure_ascii=False, default=str)

    prompt = f"""
    你是一位專業的香港言語治療師。請根據這位學生的練習歷史數據，撰寫一份**「進步分析報告」**。

    學生數據 (包含日期、分數、練習內容)：
    {data_str}

    報告要求：
    1. **語言：** 全程使用**繁體中文 (廣東話)**。
    2. **格式：** Markdown。
    3. **核心任務：**
       - **趨勢分析：** 對比「早期」數據與「近期」數據，判斷用戶是否在進步？分數有無提高？
       - **穩定性：** 用戶的表現是否穩定？還是時好時壞？
       - **具體例子：** 引用數據中的具體字詞（例如：「你以前讀『橙』得 40 分，依家有 85 分啦！」）。
       - **總結鼓勵：** 給予正面的評價和未來的方向。
    """

    try:
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return resp.text
    except Exception as e:
        print(f"Growth Report Error: {e}")
        return "try again later"


def generate_enhanced_report(data, email):
    """Generate enhanced report."""
    if not API_KEY:
        return "Error: API Key missing"
    try:
        prompt = f"你是言語治療師。根據練習數據為學生 {email} 生成廣東話分析報告。數據：{json.dumps(data, ensure_ascii=False)}"
        resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return resp.text
    except Exception as e:
        return f"failed: {str(e)}"


# ==================== Exercise Functions (Mike's full implementation) ====================

def get_random_exercise(level, category=None):
    """
    Returns a random exercise from the specified level and optional category.
    The returned dict contains at least 'word' and 'ipa' fields, and optionally 'image'.
    """
    if level not in PRONUNCIATION_EXERCISES:
        return None

    level_data = PRONUNCIATION_EXERCISES[level]
    exercises = []

    for cat, items in level_data.items():
        if category and cat != category:
            continue
        if isinstance(items, list):
            for item in items:
                norm = {}
                # Single word items
                if 'word' in item:
                    norm['word'] = item['word']
                    norm['ipa'] = item['ipa']
                # Minimal pairs (two words)
                elif 'pair' in item:
                    norm['word'] = ' vs '.join(item['pair'])
                    norm['ipa'] = '/'.join(item['ipa'])
                # Sentences / tongue twisters
                elif 'text' in item:
                    norm['word'] = item['text']
                    norm['ipa'] = item['ipa']
                else:
                    continue

                # Copy optional fields
                if 'image' in item:
                    norm['image'] = item['image']
                if 'description' in item:
                    norm['description'] = item['description']
                if 'meaning' in item:
                    norm['meaning'] = item['meaning']
                norm['category'] = cat
                exercises.append(norm)

    return random.choice(exercises) if exercises else None


def get_personalized_exercises(email, count):
    return []


def get_daily_challenges(lang='zh'):
    """Get daily challenges - seeded by date for consistency."""
    today_str = datetime.date.today().isoformat()
    random.seed(today_str)

    challenges_pool = [
        {"id": "c1", "title": {"zh": "☀️ 早晨挑戰", "en": "☀️ Morning Challenge"}, "word": "早晨", "ipa": "zou2 san4", "meaning": "Good morning", "points": 10},
        {"id": "c2", "title": {"zh": "🗣️ 生活用語", "en": "🗣️ Daily Phrases"}, "word": "唔該", "ipa": "m4 goi1", "meaning": "Excuse me / Thank you", "points": 10},
        {"id": "c3", "title": {"zh": "🍎 食物特訓", "en": "🍎 Food Training"}, "word": "蘋果", "ipa": "ping4 gwo2", "meaning": "Apple", "points": 15},
        {"id": "c4", "title": {"zh": "🏥 醫療用語", "en": "🏥 Medical Terms"}, "word": "醫生", "ipa": "ji1 sang1", "meaning": "Doctor", "points": 15},
        {"id": "c5", "title": {"zh": "🔥 急口令", "en": "🔥 Speed Challenge"}, "word": "入實驗室㩒緊急掣", "ipa": "jap6 sat6 jim6 sat1 gam6 gap1 zai3", "meaning": "Enter the lab and press the emergency button", "points": 50},
        {"id": "c6", "title": {"zh": "💪 繞口令", "en": "💪 Tongue Twister"}, "word": "郵差叔叔送信純熟迅速送出", "ipa": "jau4 caai1 suk1 suk1 sung3 seon3 seon4 suk6 seon3 cuk1 sung3 ceot1", "meaning": "The postman delivers mail skillfully and swiftly", "points": 100}
    ]

    daily_tasks = random.sample(challenges_pool, 3)
    for task in daily_tasks:
        task['title'] = task['title'].get(lang, task['title']['zh'])
    return daily_tasks
    


# ==================== Stub Functions ====================
def get_chat_response(h, u): return "..."
def generate_full_report(d): return "..."
def get_user_level(e): return "beginner"
def track_user_progress(e, t, s, d): pass
def get_user_stats(e): return {}
def get_recent_sessions(e, l): return []
def analyze_pronunciation_patterns(e, d): return {}
def export_user_data(e, f): return ""
def reset_user_progress(e): return True
def transcribe_audio_azure(f): return {}
def synthesize_cantonese_speech(text):
    import requests as _requests
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION", "eastus")

    if not key:
        print("TTS Error: AZURE_SPEECH_KEY not set")
        return None

    try:
       
        token_url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        token_resp = _requests.post(token_url, headers={"Ocp-Apim-Subscription-Key": key})
        if token_resp.status_code != 200:
            print(f"TTS token error: {token_resp.status_code} {token_resp.text}")
            return None
        token = token_resp.text

        
        tts_url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
        }
        ssml = f"""<speak version='1.0' xml:lang='zh-HK'>
            <voice name='zh-HK-HiuMaanNeural'>{text}</voice>
        </speak>"""

        resp = _requests.post(tts_url, headers=headers, data=ssml.encode("utf-8"))
        if resp.status_code == 200:
            return resp.content
        print(f"TTS error: {resp.status_code} {resp.text}")
        return None
    except Exception as e:
        print(f"TTS Exception: {e}")
        return None
def generate_practice_audio(d): return {}
def analyze_text_sentiment(t): return {}
def analyze_conversation_quality(t): return {}