#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
取引先説明会の録画・録音から文字起こしと要約を行うアプリケーション
"""

import os
import sys
import time
import tempfile
import re
import traceback
import subprocess
from pathlib import Path
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QFileDialog, QProgressBar, 
    QComboBox, QTabWidget, QSlider, QMessageBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QRadioButton, QButtonGroup, QLineEdit
)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot, QUrl, QMetaObject, Q_ARG
from PyQt5.QtGui import QIcon, QFont, QDesktopServices
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# 自作モジュールのインポート
from config.api_config import get_api_key, AVAILABLE_MODELS, DEFAULT_MODEL, MODEL_INFO
from config.prompts import (
    DEFAULT_SUMMARY_PROMPT, SHORT_SUMMARY_PROMPT, 
    DETAILED_ANALYSIS_PROMPT, load_prompt_from_file
)
from utils.whisper_utils import WhisperTranscriber
from utils.openai_utils import OpenAIAPI
from utils.document_utils import DocumentParser
from utils.audio_player import AudioPlayer

# markdown ライブラリが利用可能かどうかのフラグ
markdown_lib_available = False
try:
    import markdown
    markdown_lib_available = True
    print("Markdownライブラリは利用可能です。")
except ImportError:
    print("Markdownライブラリが見つかりません。必要に応じてインストールを試みます。")

class MainWindow(QMainWindow):
    """メインウィンドウクラス"""
    
    def __init__(self):
        super().__init__()
        
        # ウィンドウの設定
        self.setWindowTitle("取引先説明会要約ツール")
        self.setMinimumSize(1200, 800)
        
        # インスタンス変数の初期化
        self.audio_file = ""
        self.output_dir = ""
        self.document_files = []
        self.transcription = ""
        self.segments = []
        self.document_text = ""
        self.summary = ""
        self.selected_prompt_file = None # 選択されたプロンプトファイルのフルパス
        
        # ユーティリティクラスのインスタンス化
        self.transcriber = WhisperTranscriber()
        self.openai_api = OpenAIAPI()
        self.document_parser = DocumentParser()
        self.audio_player = AudioPlayer()
        
        # プログレスバーの表示用タイマー
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_progress_style)
        
        # APIキーの読み込みと設定 (環境変数から)
        try:
            api_key = get_api_key()
            self.openai_api.set_api_key(api_key)
            print("OpenAI APIキーを環境変数から正常に読み込みました。")
        except ValueError as e:
            print(f"エラー: {e}", file=sys.stderr)
            QMessageBox.critical(self, "APIキーエラー", str(e) + "\n環境変数 'OPENAI_API_KEY' を設定してください。")
            # 必要に応じてアプリケーションを終了
            sys.exit(1) # 例: アプリケーション終了
        
        # UIの初期化
        self.init_ui()
        
        # シグナル接続
        self.connect_signals()
        
    def init_ui(self):
        """UIの初期化"""
        # 中央ウィジェット
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # メインレイアウト
        main_layout = QVBoxLayout(central_widget)
        
        # ======= 上部セクション =======
        top_section = QGroupBox("音声/文書ファイル選択")
        top_layout = QVBoxLayout()
        top_section.setLayout(top_layout)
        
        # 音声ファイル選択
        audio_layout = QHBoxLayout()
        audio_label = QLabel("音声/動画ファイル:")
        self.audio_path_label = QLabel("ファイルが選択されていません")
        self.audio_path_label.setToolTip("ここに選択されたファイル名が表示されます")
        browse_audio_btn = QPushButton("音声選択...")
        browse_audio_btn.clicked.connect(self.browse_audio_file)

        # SRTファイル読込ボタンを追加
        browse_srt_btn = QPushButton("SRT読込...")
        browse_srt_btn.clicked.connect(self.browse_srt_file)

        audio_layout.addWidget(audio_label)
        audio_layout.addWidget(self.audio_path_label, 1)
        audio_layout.addWidget(browse_audio_btn)
        audio_layout.addWidget(browse_srt_btn) # ボタンをレイアウトに追加
        
        # 文書ファイル選択
        document_layout = QHBoxLayout()
        document_label = QLabel("追加資料:")
        self.document_path_label = QLabel("ファイルが選択されていません")
        document_browse_btn = QPushButton("参照...")
        document_browse_btn.clicked.connect(self.browse_document_files)
        
        document_layout.addWidget(document_label)
        document_layout.addWidget(self.document_path_label, 1)
        document_layout.addWidget(document_browse_btn)
        
        # 出力ディレクトリ選択
        output_layout = QHBoxLayout()
        output_label = QLabel("出力ディレクトリ:")
        self.output_path_label = QLabel("デフォルト")
        output_browse_btn = QPushButton("参照...")
        output_browse_btn.clicked.connect(self.browse_output_dir)
        
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_path_label, 1)
        output_layout.addWidget(output_browse_btn)
        
        # モデル選択
        model_layout = QHBoxLayout()
        model_label = QLabel("生成AIモデル:")
        self.model_combo = QComboBox()
        self.model_combo.addItems(AVAILABLE_MODELS)
        self.model_combo.setCurrentText(DEFAULT_MODEL)
        
        # モデル情報表示用ラベル
        self.model_info_label = QLabel()
        self.update_model_info(DEFAULT_MODEL)
        
        model_layout.addWidget(model_label)
        model_layout.addWidget(self.model_combo)
        model_layout.addWidget(self.model_info_label)
        model_layout.addStretch(1)
        
        # 実行ボタンセクション
        run_layout = QHBoxLayout()
        self.transcribe_btn = QPushButton("文字起こし実行")
        self.transcribe_btn.clicked.connect(self.run_transcription)
        
        self.summarize_btn = QPushButton("要約作成実行")
        self.summarize_btn.clicked.connect(self.run_summarization)
        self.summarize_btn.setEnabled(False)
        
        self.save_btn = QPushButton("結果を保存")
        self.save_btn.clicked.connect(self.save_results)
        self.save_btn.setEnabled(False)
        
        run_layout.addWidget(self.transcribe_btn)
        run_layout.addWidget(self.summarize_btn)
        run_layout.addWidget(self.save_btn)
        
        # プログレスバー
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("待機中...")
        
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        
        # 上部セクションにレイアウトを追加
        top_layout.addLayout(audio_layout)
        top_layout.addLayout(document_layout)
        top_layout.addLayout(output_layout)
        top_layout.addLayout(model_layout)
        top_layout.addLayout(run_layout)
        top_layout.addLayout(progress_layout)
        
        # ======= タブウィジェット =======
        self.tabs = QTabWidget()
        
        # 文字起こしタブ
        transcription_tab = QWidget()
        transcription_layout = QVBoxLayout(transcription_tab)
        
        # セグメントリスト
        segments_group = QGroupBox("文字起こし結果とセクション別音声再生")
        segments_layout = QVBoxLayout()
        
        # セグメントテーブル
        self.segments_table = QTableWidget(0, 4)  # 列数を5から4に変更
        self.segments_table.setHorizontalHeaderLabels(["開始時間", "終了時間", "テキスト", "再生"]) # 話者列を削除
        self.segments_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # テキスト列のインデックスを2に変更
        self.segments_table.verticalHeader().setVisible(False)
        self.segments_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.segments_table.setSelectionMode(QTableWidget.SingleSelection)
        
        # セグメントグループにウィジェットを追加
        segments_layout.addWidget(self.segments_table)
        segments_group.setLayout(segments_layout)
        
        # タブにセグメントグループを追加
        transcription_layout.addWidget(segments_group)
        
        # 追加資料タブ
        document_tab = QWidget()
        document_layout = QVBoxLayout(document_tab)
        self.document_text_edit = QTextEdit()
        self.document_text_edit.setReadOnly(True)
        document_layout.addWidget(self.document_text_edit)
        
        # 要約タブ
        summary_tab = QWidget()
        summary_layout = QVBoxLayout(summary_tab)
        
        # 要約オプション
        summary_options = QHBoxLayout()
        
        # プロンプト選択
        prompt_group = QGroupBox("要約タイプ")
        prompt_layout = QVBoxLayout()
        self.prompt_buttons = QButtonGroup()
        
        self.default_prompt_btn = QRadioButton("標準要約")
        self.default_prompt_btn.setChecked(True)
        self.short_prompt_btn = QRadioButton("短い要約")
        self.detailed_prompt_btn = QRadioButton("詳細分析")
        self.custom_prompt_btn = QRadioButton("カスタムプロンプト")
        
        self.prompt_buttons.addButton(self.default_prompt_btn)
        self.prompt_buttons.addButton(self.short_prompt_btn)
        self.prompt_buttons.addButton(self.detailed_prompt_btn)
        self.prompt_buttons.addButton(self.custom_prompt_btn)
        
        prompt_layout.addWidget(self.default_prompt_btn)
        prompt_layout.addWidget(self.short_prompt_btn)
        prompt_layout.addWidget(self.detailed_prompt_btn)
        prompt_layout.addWidget(self.custom_prompt_btn)
        
        # カスタムプロンプト入力エリア
        self.custom_prompt_area = QTextEdit()
        self.custom_prompt_area.setPlaceholderText("ここにカスタムプロンプトを入力できます。{transcription}は文字起こし内容、{additional_info}は追加資料として参照されます。")
        self.custom_prompt_area.setMaximumHeight(150)
        self.custom_prompt_area.setEnabled(False)
        
        # カスタムプロンプト選択時に入力エリアを有効化する接続
        self.custom_prompt_btn.toggled.connect(self.toggle_custom_prompt_area)
        
        prompt_layout.addWidget(self.custom_prompt_area)
        
        # ファイルからプロンプトを読み込むオプション
        prompt_file_layout = QHBoxLayout()
        prompt_file_label = QLabel("または、プロンプトファイル:")
        self.prompt_file_path = QLabel("未選択")
        prompt_browse_btn = QPushButton("参照...")
        prompt_browse_btn.clicked.connect(self.browse_prompt_file)
        
        prompt_file_layout.addWidget(prompt_file_label)
        prompt_file_layout.addWidget(self.prompt_file_path, 1)
        prompt_file_layout.addWidget(prompt_browse_btn)
        
        prompt_layout.addLayout(prompt_file_layout)
        prompt_group.setLayout(prompt_layout)
        
        summary_options.addWidget(prompt_group)
        
        # 要約テキスト
        self.summary_text = QTextEdit()
        
        summary_layout.addLayout(summary_options)
        summary_layout.addWidget(self.summary_text)
        
        # タブの追加
        self.tabs.addTab(transcription_tab, "文字起こし")
        self.tabs.addTab(document_tab, "追加資料")
        self.tabs.addTab(summary_tab, "要約")
        
        # メインレイアウトに追加
        main_layout.addWidget(top_section)
        main_layout.addWidget(self.tabs, 1)
    
    def connect_signals(self):
        """シグナルとスロットの接続"""
        # Whisper文字起こし進捗
        self.transcriber.progress_updated.connect(self.update_transcribe_progress)
        
        # Whisperのセグメント更新シグナル
        self.transcriber.segment_updated.connect(self.on_segment_updated)
        
        # OpenAI API進捗
        self.openai_api.progress_updated.connect(self.update_summarize_progress)
        
        # ドキュメントパーサー進捗
        self.document_parser.progress_updated.connect(self.update_document_progress)
        
        # 音声プレーヤーシグナル
        self.audio_player.position_changed.connect(self.update_position)
        self.audio_player.duration_changed.connect(self.update_duration)
        self.audio_player.state_changed.connect(self.update_state)
        self.audio_player.error_occurred.connect(self.on_audio_error)
        
        # モデル選択変更時
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        
        # Whisper文字起こし完了シグナル
        self.transcriber.transcription_finished.connect(self.on_transcription_finished)
    
    def browse_audio_file(self):
        """音声/動画ファイルを選択するダイアログを表示"""
        file_dialog = QFileDialog()
        file_path, _ = file_dialog.getOpenFileName(
            self, "音声/動画ファイルを選択", "", 
            "音声/動画ファイル (*.mp3 *.wav *.ogg *.mp4 *.avi *.mov *.m4a);;すべてのファイル (*)"
        )
        
        if file_path:
            # 読み込み中表示
            self.progress_label.setText("音声ファイル読み込み中...")
            self.progress_bar.setValue(10)
            self.audio_path_label.setText("読み込み中...")
            
            # 絶対パスに変換
            file_path = os.path.abspath(file_path)
            self.audio_file = file_path
            
            # アプリケーションイベントを処理
            QApplication.processEvents()
            
            # 音声ファイルをプレーヤーに読み込み
            if self.audio_player.load_file(file_path):
                self.audio_path_label.setText(os.path.basename(file_path))
                # プログレスバーとラベルを初期化
                self.progress_bar.setValue(0)
                self.progress_label.setText("待機中...") 
                # スタイルも初期状態に戻す（必要であれば）
                self.progress_bar.setStyleSheet("") # デフォルトスタイルに戻す
            else:
                self.audio_path_label.setText("読み込みに失敗しました")
                self.audio_file = ""
                self.progress_label.setText("音声ファイル読み込み失敗")
                self.progress_bar.setValue(0)
    
    def browse_document_files(self):
        """追加資料ファイルを選択するダイアログを表示"""
        file_dialog = QFileDialog()
        file_paths, _ = file_dialog.getOpenFileNames(
            self, "追加資料を選択", "", 
            "ドキュメントファイル (*.pdf *.docx *.pptx *.txt);;すべてのファイル (*)"
        )
        
        if file_paths:
            self.document_files = file_paths
            self.document_path_label.setText(f"{len(file_paths)}個のファイルを選択")
    
    def browse_output_dir(self):
        """出力ディレクトリを選択するダイアログを表示"""
        dir_dialog = QFileDialog()
        dir_path = dir_dialog.getExistingDirectory(self, "出力ディレクトリを選択")
        
        if dir_path:
            self.output_dir = dir_path
            self.output_path_label.setText(dir_path)
    
    def browse_prompt_file(self):
        """プロンプトファイルを選択するダイアログを表示"""
        file_dialog = QFileDialog()
        file_path, _ = file_dialog.getOpenFileName(
            self, "プロンプトファイルを選択", "", 
            "テキストファイル (*.txt);;すべてのファイル (*)"
        )
        
        if file_path:
            self.selected_prompt_file = os.path.abspath(file_path) # フルパスを保存
            self.prompt_file_path.setText(os.path.basename(file_path)) # ラベルにはファイル名を表示
            self.prompt_file_path.setToolTip(self.selected_prompt_file) # ツールチップにフルパス表示
            self.custom_prompt_btn.setChecked(True) # ファイル選択したらカスタムラジオを選択状態にする
            self.custom_prompt_area.clear() # テキストエリアはクリアする
            print(f"カスタムプロンプトファイル選択: {self.selected_prompt_file}")
        else:
            # キャンセルされた場合、以前選択していたファイルパスをクリア
            # self.selected_prompt_file = None
            # self.prompt_file_path.setText("未選択")
            # self.prompt_file_path.setToolTip("")
            pass # 選択がキャンセルされた場合は何もしない方が良い場合もある
    
    def toggle_custom_prompt_area(self, checked):
        """カスタムプロンプト入力エリアの有効/無効を切り替え"""
        self.custom_prompt_area.setEnabled(checked)
        if checked:
            self.custom_prompt_area.setFocus()
            
    def update_model_info(self, model_name):
        """モデル情報表示を更新"""
        if model_name in MODEL_INFO:
            info = MODEL_INFO[model_name]
            self.model_info_label.setText(f"({info['description']} / コスト: {info['cost_per_1k']})")
        else:
            self.model_info_label.setText("")
            
    def on_model_changed(self, model_name):
        """モデル選択時の処理"""
        self.openai_api.set_model(model_name)
        self.update_model_info(model_name)
    
    def run_transcription(self):
        """
        音声ファイルの文字起こしを実行する
        """
        if not self.audio_file:
            QMessageBox.warning(self, "エラー", "音声ファイルが選択されていません。")
            return

        # プログレスバーをリセット
        self.progress_bar.setValue(0)
        self.progress_label.setText("文字起こしの準備中...")
        
        # 変数クリア
        self.transcription = ""
        self.segments = []
        
        try:
            # 音声ファイルの絶対パスを取得
            audio_file_path = os.path.abspath(self.audio_file)
            
            # ボタンを無効化
            self.transcribe_btn.setEnabled(False)
            
            # トランスクライバーのシグナル接続を確認
            # プログラム開始時に既に接続されているはずだが念のため
            try:
                self.transcriber.progress_updated.disconnect()
                self.transcriber.transcription_finished.disconnect()
            except:
                pass
                
            self.transcriber.progress_updated.connect(self.update_transcribe_progress)
            self.transcriber.transcription_finished.connect(self.on_transcription_finished)
            
            # 文字起こし開始
            self.transcriber.transcribe(audio_file_path, output_dir=self.output_dir)
            
            # プログレスバーのアニメーションを開始
            self.progress_timer.start(200)
            
        except Exception as e:
            traceback.print_exc()
            self.progress_label.setText(f"文字起こし失敗: {str(e)}")
            self.transcribe_btn.setEnabled(True)
            QMessageBox.critical(self, "エラー", f"文字起こし実行中にエラーが発生しました: {str(e)}")
    
    def process_documents(self):
        """追加資料の処理"""
        self.progress_bar.setValue(0)
        self.progress_label.setText("追加資料の処理を開始します...")
        
        all_text = ""
        for doc_file in self.document_files:
            self.progress_label.setText(f"処理中: {os.path.basename(doc_file)}")
            text = self.document_parser.extract_text_from_file(doc_file)
            all_text += f"\n--- {os.path.basename(doc_file)} ---\n{text}\n\n"
        
        self.document_text = all_text
        self.document_text_edit.setText(all_text)
        
        self.progress_bar.setValue(100)
        self.progress_label.setText("追加資料の処理完了")
    
    def populate_segments(self, segments, has_audio=True):
        """セグメントテーブルにデータを設定 (音声有無フラグ付き)"""
        self.segments_table.setRowCount(len(segments))
        
        for i, segment in enumerate(segments):
            # 開始時間
            start_item = QTableWidgetItem(self.format_time(segment.get('start', 0)))
            self.segments_table.setItem(i, 0, start_item)
            
            # 終了時間
            end_item = QTableWidgetItem(self.format_time(segment.get('end', 0)))
            self.segments_table.setItem(i, 1, end_item)
            
            # テキスト (列インデックスを2に変更)
            text_item = QTableWidgetItem(segment.get('text', ''))
            self.segments_table.setItem(i, 2, text_item)
            
            # 再生ボタン (列インデックスを3に変更)
            play_button = QPushButton("再生")
            if has_audio:
                play_button.clicked.connect(lambda checked, s=segment: self.play_segment(s))
                play_button.setEnabled(True)
            else:
                play_button.setEnabled(False) # 音声がない場合は無効化
            self.segments_table.setCellWidget(i, 3, play_button)
    
    def format_time(self, seconds):
        """秒数を「分:秒」形式にフォーマット"""
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"
    
    def on_segment_selected(self):
        """セグメント選択時の処理"""
        selected_items = self.segments_table.selectedItems()
        if not selected_items:
            return
        
        row = selected_items[0].row()
        segment = self.segments[row]
        start_time = segment.get('start', 0)
        
        # 音声プレーヤーに再生位置をセット
        self.audio_player.set_position(int(start_time * 1000))
    
    def play_segment(self, segment):
        """セグメントを再生"""
        if not self.audio_file or not self.segments:
            return
        
        start_time = segment.get('start', 0)
        end_time = segment.get('end', 0)
        
        # 開始時間をミリ秒に変換
        start_time_ms = int(start_time * 1000)
        
        # playメソッド内で位置設定を行うため、ここでのset_position呼び出しを削除
        # self.audio_player.set_position(start_time_ms)
        
        # セグメントの終了時間を設定
        self.audio_player.set_end_position(int(end_time * 1000))
        
        # 再生開始 (開始位置を引数で渡す)
        self.audio_player.play(start_time_ms)
    
    def play_audio(self):
        """音声を再生"""
        self.audio_player.play()
    
    def pause_audio(self):
        """音声を一時停止"""
        self.audio_player.pause()
    
    def stop_audio(self):
        """音声を停止"""
        self.audio_player.stop()
    
    def set_position(self, position):
        """再生位置の更新"""
        # 現在の位置に該当するセグメントを検索してハイライト
        position_seconds = position / 1000
        for i, segment in enumerate(self.segments):
            if segment.get('start', 0) <= position_seconds <= segment.get('end', 0):
                self.segments_table.selectRow(i)
                break
    
    def update_duration(self, duration):
        """音声の長さの更新"""
        pass  # 何もしない

    def update_position(self, position):
        """再生位置の更新"""
        # 現在の位置に該当するセグメントを検索してハイライト
        position_seconds = position / 1000
        for i, segment in enumerate(self.segments):
            if segment.get('start', 0) <= position_seconds <= segment.get('end', 0):
                self.segments_table.selectRow(i)
                break

    def update_state(self, state):
        """プレーヤーの状態更新"""
        pass  # 何もしない
    
    def run_summarization(self):
        """要約処理を実行 (Markdown -> HTML変換修正)"""
        if not self.transcription: return
        if not self.openai_api.api_key: return

        # プロンプトの選択
        prompt = None
        if self.default_prompt_btn.isChecked(): prompt = DEFAULT_SUMMARY_PROMPT
        elif self.short_prompt_btn.isChecked(): prompt = SHORT_SUMMARY_PROMPT
        elif self.detailed_prompt_btn.isChecked(): prompt = DETAILED_ANALYSIS_PROMPT
        elif self.custom_prompt_btn.isChecked():
            custom_text = self.custom_prompt_area.toPlainText().strip()
            if custom_text: prompt = custom_text
            elif self.selected_prompt_file and os.path.exists(self.selected_prompt_file):
                try: prompt = load_prompt_from_file(self.selected_prompt_file)
                except Exception as e: QMessageBox.critical(self, "エラー", f"プロンプト読込エラー: {e}"); return
            if not prompt: QMessageBox.warning(self, "警告", "カスタムプロンプト未入力/未選択"); return
        if not prompt: QMessageBox.critical(self, "エラー", "プロンプト未決定"); return

        # ボタン無効化、進捗表示初期化
        self.summarize_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("要約を開始します...")
        self.progress_timer.start(200)
        
        # モデル名取得
        model = self.model_combo.currentText()
        self.openai_api.set_model(model)
        
        self.progress_bar.setValue(10)
        self.progress_label.setText("OpenAI API に要約リクエストを送信中...")
        QApplication.processEvents() # UI更新
        
        # 要約実行
        try:
            self.summary = self.openai_api.generate_summary(
                prompt, 
                self.transcription, 
                self.document_text
            )
        except Exception as api_e:
             QMessageBox.critical(self, "APIエラー", f"OpenAI APIとの通信中にエラーが発生しました。\n{api_e}")
             self.progress_label.setText("APIエラー")
             self.progress_bar.setValue(0)
             self.progress_timer.stop()
             self.summarize_btn.setEnabled(True)
             return

        # 要約結果を表示
        self.progress_bar.setValue(90)
        self.progress_label.setText("要約結果を処理中...")
        QApplication.processEvents() # UI更新

        if self.summary:
            html_content = None
            try:
                # --- HTML変換試行 --- 
                import markdown # ここでインポートを試みる
                html_content = markdown.markdown(self.summary, extensions=['extra', 'nl2br'])
                print("要約をHTMLとして表示します")
                self.summary_text.setHtml(html_content)
                # ---------------------
            except ImportError:
                # --- インポート失敗時のインストール試行 --- 
                print("Markdownライブラリが見つかりません。インストールを試みます。")
                reply = QMessageBox.question(self, '確認', 
                                             '要約表示に必要なMarkdownライブラリが見つかりません。\n'
                                             'インストールを試みますか？ (pip install markdown)', 
                                             QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                
                if reply == QMessageBox.Yes:
                    self.progress_label.setText("Markdownライブラリをインストール中...")
                    QApplication.processEvents()
                    try:
                        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'markdown'])
                        print("Markdownライブラリのインストール成功。再試行します。")
                        # 再度インポートと変換を試みる
                        try:
                             import markdown
                             html_content = markdown.markdown(self.summary, extensions=['extra', 'nl2br'])
                             print("要約をHTMLとして表示します (インストール後)")
                             self.summary_text.setHtml(html_content)
                        except Exception as e_inner:
                             print(f"インストール後のMarkdown変換/表示エラー: {e_inner}")
                             QMessageBox.warning(self, "エラー", "Markdown変換に失敗。テキスト形式で表示。")
                             self.summary_text.setText(self.summary) # フォールバック
                    except Exception as e_install:
                        QMessageBox.critical(self, "エラー", f"Markdownライブラリのインストール失敗。\n{e_install}")
                        self.summary_text.setText(self.summary) # フォールバック
                else:
                    print("インストールをスキップ。テキスト形式で表示します。")
                    self.summary_text.setText(self.summary) # フォールバック
                # ---------------------------------------
            except Exception as e_convert:
                # --- その他の変換エラー --- 
                print(f"MarkdownからHTMLへの変換エラー: {e_convert}")
                self.summary_text.setText(self.summary) # フォールバック
                QMessageBox.warning(self, "表示エラー", "要約のHTML表示に失敗。Markdown形式で表示。")
                # ---------------------------
        else:
             self.summary_text.clear()
             print("要約結果が空でした")
        
        # タブ切り替え、進捗完了、ボタン有効化
        self.tabs.setCurrentIndex(2)
        self.progress_bar.setValue(100)
        self.progress_label.setText("要約完了")
        self.progress_timer.stop()
        self.summarize_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
    
    def save_results(self):
        """結果を保存"""
        if not self.transcription and not self.summary:
            QMessageBox.warning(self, "警告", "保存する結果がありません")
            return
        
        # 出力ディレクトリの設定
        output_dir = self.output_dir if self.output_dir else os.path.join(os.path.expanduser("~"), "Documents", "要約ツール")
        os.makedirs(output_dir, exist_ok=True)
        
        # 現在の日時
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # ファイル名の設定
        base_name = os.path.splitext(os.path.basename(self.audio_file))[0] if self.audio_file else "transcription"
        
        # 文字起こしの保存
        if self.transcription:
            transcription_file = os.path.join(output_dir, f"{base_name}_{timestamp}_transcription.txt")
            with open(transcription_file, "w", encoding="utf-8") as f:
                f.write(self.transcription)
        
        # 要約の保存
        if self.summary:
            summary_file = os.path.join(output_dir, f"{base_name}_{timestamp}_summary.txt")
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(self.summary)
        
        QMessageBox.information(self, "完了", f"結果を保存しました\n保存先: {output_dir}")
    
    def update_transcribe_progress(self, value, message):
        """文字起こしの進捗更新"""
        # GUIの更新はメインスレッドで行われるようにする
        QMetaObject.invokeMethod(
            self.progress_bar, 
            "setValue", 
            Qt.QueuedConnection,
            Q_ARG(int, value)
        )
        
        # ラベルの更新
        QMetaObject.invokeMethod(
            self.progress_label, 
            "setText", 
            Qt.QueuedConnection,
            Q_ARG(str, message)
        )
    
    def update_summarize_progress(self, value, message):
        """要約の進捗更新"""
        # GUIの更新はメインスレッドで行われるようにする
        QMetaObject.invokeMethod(
            self.progress_bar, 
            "setValue", 
            Qt.QueuedConnection,
            Q_ARG(int, value)
        )
        
        # ラベルの更新
        QMetaObject.invokeMethod(
            self.progress_label, 
            "setText", 
            Qt.QueuedConnection,
            Q_ARG(str, message)
        )
        
        # アプリケーションのイベントを処理
        QApplication.processEvents()
    
    def update_document_progress(self, value, message):
        """文書処理の進捗更新"""
        # GUIの更新はメインスレッドで行われるようにする
        QMetaObject.invokeMethod(
            self.progress_bar, 
            "setValue", 
            Qt.QueuedConnection,
            Q_ARG(int, value)
        )
        
        # ラベルの更新
        QMetaObject.invokeMethod(
            self.progress_label, 
            "setText", 
            Qt.QueuedConnection,
            Q_ARG(str, message)
        )
        
        # アプリケーションのイベントを処理
        QApplication.processEvents()
    
    def update_progress_style(self):
        """プログレスバーのスタイル更新（アニメーション効果）"""
        value = self.progress_bar.value()
        max_value = self.progress_bar.maximum()
        
        # 値が変わった場合のみスタイルを更新する
        if not hasattr(self, 'last_progress_value') or self.last_progress_value != value:
            self.last_progress_value = value
            
            if value == max_value:
                self.progress_bar.setStyleSheet("QProgressBar { background-color: #e0e0e0; border: 1px solid #bdbdbd; border-radius: 5px; text-align: center; } QProgressBar::chunk { background-color: #4CAF50; border-radius: 5px; }")
            else:
                self.progress_bar.setStyleSheet("QProgressBar { background-color: #e0e0e0; border: 1px solid #bdbdbd; border-radius: 5px; text-align: center; } QProgressBar::chunk { background-color: #2196F3; border-radius: 5px; }")

    def on_transcription_finished(self, text, segments, success):
        """文字起こし完了時の処理"""
        if success:
            # 文字起こし結果を表示
            self.transcription = text
            self.segments = segments
            self.populate_segments(segments)
            self.summarize_btn.setEnabled(True)
            
            # 文書ファイルがあれば処理
            if self.document_files:
                self.process_documents()
        else:
            if text:  # エラーメッセージがある場合
                QMessageBox.critical(self, "エラー", text)
            else:
                QMessageBox.critical(self, "エラー", "文字起こしに失敗しました")
        
        # 進捗バーを完了状態に
        self.progress_bar.setValue(100)
        self.progress_label.setText("文字起こし" + ("完了" if success else "失敗"))
        self.progress_timer.stop()
        
        # ボタンの有効化
        self.transcribe_btn.setEnabled(True)

    def on_audio_error(self, error_message):
        """音声プレーヤーのエラー処理"""
        print(f"音声再生エラー: {error_message}")
        # QMessageBox.warning(self, "再生エラー", f"音声の再生中にエラーが発生しました。\n詳細: {error_message}")
        # エラー発生時にボタンの状態を適切に設定する
        # self.play_btn.setEnabled(True) # play_btn は存在しない
        self.stop_audio_btn.setEnabled(True) # 停止ボタンは有効にするなど
        # 他の関連ボタンの状態も必要に応じて更新

    def on_segment_updated(self, stdout):
        """Whisperのセグメント更新シグナルを処理"""
        # 時間情報からの進捗更新はWhisperTranscriberのextract_timestamp_progressで処理されるため、
        # ここでの処理は最小限にする
        # 時間情報が見つからなかった場合のみ、セグメント情報からの進捗更新を試みる
        if not self.handle_whisper_progress(stdout):
            # 他の進捗情報処理（必要に応じて）
            pass
        
    def handle_whisper_progress(self, stdout):
        """Whisperの出力から進捗を推定し、プログレスバーを更新する"""
        # 時間情報を検出
        import re
        timestamp_pattern = r'\[(\d{2}):(\d{2}):([\d\.]+)\s+-->\s+(\d{2}):(\d{2}):([\d\.]+)\]'
        if re.search(timestamp_pattern, stdout):
            # 時間情報は既にWhisperTranscriberで処理されているため、見つかったことだけを返す
            return True
            
        # セグメント情報を検出（時間情報が見つからない場合のフォールバック）
        if "segment" in stdout.lower():
            try:
                # "Processing segment X / Y"のようなフォーマットから進捗を抽出
                parts = stdout.split("segment")[1].strip().split("/")
                if len(parts) == 2:
                    current = int(parts[0].strip())
                    total = int(parts[1].strip().split()[0])
                    
                    # 進捗率を計算 (20%〜80%の範囲で)
                    progress = 20 + int(60 * current / total)
                    self.progress_bar.setValue(progress)
                    self.progress_label.setText(f"文字起こし中... セグメント {current}/{total}")
                    return True
            except Exception as e:
                print(f"進捗解析エラー: {str(e)}")
                
        return False

    def closeEvent(self, event):
        """ウィンドウが閉じられるときのイベント"""
        self.audio_player.cleanup() # AudioPlayerのクリーンアップを呼び出す
        event.accept() # イベントを受け入れてウィンドウを閉じる

    def browse_srt_file(self):
        """SRTファイルを選択するダイアログを表示"""
        file_dialog = QFileDialog()
        srt_path, _ = file_dialog.getOpenFileName(
            self, "SRTファイルを選択", "",
            "SRTファイル (*.srt);;すべてのファイル (*)"
        )

        if srt_path:
            self.progress_label.setText("SRTファイルを読み込み中...")
            self.progress_bar.setValue(10)
            QApplication.processEvents()
            self.load_srt_data(srt_path)

    def load_srt_data(self, srt_path):
        """指定されたSRTファイルを読み込み、表示を更新する"""
        try:
            print(f"SRTファイル読み込み開始: {srt_path}")
            segments = self._parse_srt_file_main(srt_path)

            if not segments:
                QMessageBox.warning(self, "SRT読み込みエラー", "SRTファイルの解析に失敗しました。ファイル形式を確認してください。")
                self.progress_label.setText("SRT読み込み失敗")
                self.progress_bar.setValue(0)
                return

            self.segments = segments
            # 全体の文字起こしテキストを生成
            self.transcription = "".join([seg.get('text', '').strip() + "\n" for seg in segments])

            # 音声関連情報をクリア
            self.audio_file = None
            self.audio_path_label.setText(f"SRT読込: {os.path.basename(srt_path)}")
            self.audio_path_label.setToolTip(srt_path)
            self.audio_player.stop() # 既存の再生を停止
            # self.audio_player.setMedia(QMediaContent()) # メディアをクリア (必要に応じて)

            # セグメント表示 (音声なしフラグを立てる)
            self.populate_segments(self.segments, has_audio=False)

            # ボタンの状態更新
            self.summarize_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(False) # SRT読み込み時は文字起こしボタンを無効化

            # タブを文字起こしタブに切り替え
            self.tabs.setCurrentIndex(0)

            self.progress_bar.setValue(100)
            self.progress_label.setText("SRTファイルの読み込み完了")
            print("SRTファイル読み込み成功")

        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "SRT読み込みエラー", f"SRTファイルの読み込み中に予期せぬエラーが発生しました:\n{e}")
            self.progress_label.setText("SRT読み込みエラー")
            self.progress_bar.setValue(0)

    def _parse_srt_file_main(self, srt_file_path):
        """SRTファイルを解析してセグメントリストを生成 (MainWindow用)"""
        segments = []
        try:
            with open(srt_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            entries = content.strip().split('\n\n')
            for entry in entries:
                lines = entry.split('\n')
                if len(lines) >= 3:
                    time_range = lines[1]
                    start_time, end_time = self._parse_srt_time_range_main(time_range)
                    text = ' '.join(lines[2:])
                    segment = {'start': start_time, 'end': end_time, 'text': text}
                    segments.append(segment)
            return segments
        except Exception as e:
            print(f"SRTファイル解析エラー (main): {str(e)}")
            return []

    def _parse_srt_time_range_main(self, time_range):
        """SRTの時間範囲文字列をパース (MainWindow用)"""
        try:
            start_str, end_str = time_range.split(' --> ')
            start_time = self._parse_srt_time_main(start_str)
            end_time = self._parse_srt_time_main(end_str)
            return start_time, end_time
        except:
            return 0, 0

    def _parse_srt_time_main(self, time_str):
        """SRTの時間文字列を秒に変換 (MainWindow用)"""
        try:
            parts = time_str.split(':')
            h = int(parts[0])
            m = int(parts[1])
            s_ms = parts[2].replace(',', '.')
            s, ms = map(float, s_ms.split('.'))
            return h * 3600 + m * 60 + s + ms / 1000.0
        except:
            return 0

def main():
    """メイン関数"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main() 