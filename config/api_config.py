"""
OpenAI APIキーと設定を管理するファイル
"""

# APIキーを保存するファイルのパス（デフォルト）
API_KEY_FILE = "config/openai_api_key.txt"

def load_api_key(file_path=None):
    """
    APIキーをファイルから読み込む
    
    Args:
        file_path (str, optional): APIキーファイルのパス
        
    Returns:
        str: APIキー
    """
    path = file_path or API_KEY_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

# モデル設定
DEFAULT_MODEL = "gpt-4o"
AVAILABLE_MODELS = [
    "gpt-3.5-turbo",    # 低コスト、高速
    "gpt-4-turbo",      # バランス型
    "gpt-4o",           # 最新モデル
]

# モデルの詳細情報
MODEL_INFO = {
    "gpt-3.5-turbo": {
        "name": "GPT-3.5 Turbo",
        "description": "低コストで高速な処理が可能なモデル",
        "cost_per_1k": "0.5円〜",
        "token_limit": 16385,
    },
    "gpt-4-turbo": {
        "name": "GPT-4 Turbo",
        "description": "高品質な要約が可能なバランス型モデル",
        "cost_per_1k": "15円〜",
        "token_limit": 128000,
    },
    "gpt-4o": {
        "name": "GPT-4o",
        "description": "最新の高性能モデル",
        "cost_per_1k": "15円〜",
        "token_limit": 128000,
    },
}

# Whisper設定
WHISPER_PATH = "Faster-Whisper-XXL" 