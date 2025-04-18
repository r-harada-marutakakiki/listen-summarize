"""
要約指示用のプロンプトテンプレートを管理するファイル
"""

# デフォルト要約プロンプト
DEFAULT_SUMMARY_PROMPT = """
以下は取引先説明会の文字起こしです。この内容を要約してください。

【要約の要件】
- 説明会の主要なポイントを箇条書きでまとめてください
- 需要の見込みについての説明をまとめてください
- 生産ライン、モデル別の生産台数をまとめてください
- 質疑応答のセクションから重要な質問と回答をまとめてください
- 今後のTODOや次のステップがあれば抽出してください

【文字起こし】
{transcription}

【追加資料からの情報】
{additional_info}
"""

# 短い要約用プロンプト
SHORT_SUMMARY_PROMPT = """
以下は取引先説明会の文字起こしです。200字以内の簡潔な要約を作成してください。

【文字起こし】
{transcription}

【追加資料からの情報】
{additional_info}
"""

# 詳細な分析用プロンプト
DETAILED_ANALYSIS_PROMPT = """
以下は取引先説明会の文字起こしです。この内容について詳細な分析を行ってください。

【分析の要件】
- 説明会の主要なポイントを詳細に分析してください
- 需要の見込みについての説明をまとめてください
- 生産ライン、モデル別の生産台数をまとめてください
- 質疑応答のセクションから重要な質問と回答をまとめてください
- 今後のTODOや次のステップがあれば抽出してください

【文字起こし】
{transcription}

【追加資料からの情報】
{additional_info}
"""

def load_prompt_from_file(file_path):
    """
    プロンプトをファイルから読み込む
    
    Args:
        file_path (str): プロンプトファイルのパス
        
    Returns:
        str: プロンプトテンプレート
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return DEFAULT_SUMMARY_PROMPT 