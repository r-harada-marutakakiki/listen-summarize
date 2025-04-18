"""
OpenAI APIと通信するためのユーティリティ関数
"""

import os
import openai
from PyQt5.QtCore import QObject, pyqtSignal

class OpenAIAPI(QObject):
    """OpenAI APIとの通信を行うクラス"""
    
    progress_updated = pyqtSignal(int, str)
    
    def __init__(self, api_key=""):
        super().__init__()
        self.api_key = api_key
        self.model = "gpt-4-turbo"
        
    def set_api_key(self, api_key):
        """
        APIキーを設定する
        
        Args:
            api_key (str): OpenAI APIキー
        """
        self.api_key = api_key
        
    def set_model(self, model):
        """
        使用するモデルを設定する
        
        Args:
            model (str): OpenAIのモデル名
        """
        self.model = model
    
    def generate_summary(self, prompt, transcription, additional_info=""):
        """
        文字起こしと追加情報から要約を生成する
        
        Args:
            prompt (str): 要約用プロンプトテンプレート
            transcription (str): 文字起こしテキスト
            additional_info (str, optional): 追加資料からの情報
            
        Returns:
            str: 生成された要約
        """
        if not self.api_key:
            return "APIキーが設定されていません。"
        
        self.progress_updated.emit(10, "OpenAI APIに接続中...")
        
        openai.api_key = self.api_key
        
        # プロンプトを整形
        formatted_prompt = prompt.format(
            transcription=transcription,
            additional_info=additional_info
        )
        
        self.progress_updated.emit(30, "要約を生成中...")
        
        try:
            client = openai.OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "あなたは取引先説明会の要約を作成する専門家です。"},
                    {"role": "user", "content": formatted_prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            self.progress_updated.emit(90, "要約完了")
            return response.choices[0].message.content
            
        except Exception as e:
            self.progress_updated.emit(100, f"エラー: {str(e)}")
            return f"要約生成中にエラーが発生しました: {str(e)}" 