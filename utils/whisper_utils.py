"""
Whisperを使用して音声から文字起こしを行うためのユーティリティ
"""

import os
import subprocess
import json
import tempfile
from PyQt5.QtCore import QObject, pyqtSignal, QThread, QProcess, QTimer
from config.api_config import WHISPER_PATH
import threading
from datetime import datetime
import re
import sys
import time
import traceback

# スクリプトの場所に基づいて ffmpeg/ffprobe のパスを決定
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir) # utilsの一つ上の階層
ffmpeg_dir_name = "Faster-Whisper-XXL"

ffmpeg_exe_name = "ffmpeg.exe"
ffmpeg_path = os.path.join(project_root, ffmpeg_dir_name, ffmpeg_exe_name)
print(f"使用するffmpegのパス: {ffmpeg_path}")

ffprobe_exe_name = "ffprobe.exe" # ffprobeの名前
ffprobe_path = os.path.join(project_root, ffmpeg_dir_name, ffprobe_exe_name)
print(f"使用するffprobeのパス: {ffprobe_path}")

class WhisperTranscriber(QObject):
    """Whisperを使用して音声ファイルから文字起こしを行うクラス"""
    
    progress_updated = pyqtSignal(int, str)
    transcription_finished = pyqtSignal(str, list, bool)  # 文字起こし完了シグナル：(テキスト, セグメント, 成功フラグ)
    segment_updated = pyqtSignal(str)  # セグメント更新シグナル：現在処理中のセグメント情報を送信
    
    def __init__(self, whisper_path=None):
        super().__init__()
        self.whisper_path = whisper_path or WHISPER_PATH
        self.segments = []
        self.speakers = []
        self.process = None
        self.output_directory = None
        self.log_file = None
        self.current_segment = 0
        self.total_segments = 0
        self.audio_duration = 0  # 音声ファイルの総再生時間（秒）
        self.current_timestamp = 0  # 現在処理中の時間位置（秒）
        self.last_progress_percent = 0  # 最後に報告された進捗率を保存
        self.expected_srt_filename = ""  # 期待されるSRTファイル名
        
    def get_audio_duration(self, file_path):
        """ffprobeを使用して音声ファイルの長さを秒単位で取得する"""
        if not os.path.exists(ffprobe_path):
            print(f"ffprobeが見つかりません: {ffprobe_path}")
            return 0
        if not os.path.exists(file_path):
            print(f"音声ファイルが見つかりません: {file_path}")
            return 0

        try:
            ffprobe_cmd = [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "a:0", # 最初のオーディオストリームを選択
                "-show_entries", "format=duration",
                "-of", "json",
                file_path
            ]
            print(f"ffprobeコマンド実行: {' '.join(ffprobe_cmd)}")
            result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            
            if result.returncode != 0:
                print(f"ffprobe実行エラー (Code: {result.returncode}):\n{result.stderr}")
                return 0

            # JSON出力をパース
            output_data = json.loads(result.stdout)
            if "format" in output_data and "duration" in output_data["format"]:
                duration = float(output_data["format"]["duration"])
                print(f"ffprobeで取得したDuration: {duration:.3f}秒")
                return duration
            else:
                print("ffprobeのJSON出力にDurationが含まれていません。")
                print(f"ffprobe出力: {result.stdout}")
                return 0
                
        except FileNotFoundError:
            print(f"ffprobeの実行に失敗しました。パスを確認してください: {ffprobe_path}")
            return 0
        except json.JSONDecodeError:
            print(f"ffprobeのJSON出力のパースに失敗しました:\n{result.stdout}")
            return 0
        except Exception as e:
            print(f"音声長さの取得中に予期せぬエラー: {e}")
            traceback.print_exc()
            return 0

    def transcribe(self, audio_file_path, output_dir=None, diarize=True):
        """
        音声ファイルから文字起こしを行う
        
        Args:
            audio_file_path (str): 音声ファイルのパス
            output_dir (str, optional): 出力ディレクトリ
            diarize (bool, optional): 話者分離を行うかどうか
        """
        # --- 処理開始時に必ずリセット --- 
        self.last_progress_percent = 0 
        self.segments = [] # セグメントリストも初期化
        self.audio_duration = 0 # 音声長も初期化
        self.expected_srt_filename = "" # 期待ファイル名も初期化
        # ----------------------------------

        # プログレスバーをリセット (UI側への通知)
        self.progress_updated.emit(0, "文字起こしの準備中...")
        
        if not os.path.exists(audio_file_path):
            self.progress_updated.emit(100, f"ファイルが見つかりません: {audio_file_path}")
            self.transcription_finished.emit("", [], False)
            return
            
        # 音声ファイルの長さを取得
        try:
            # mutagenライブラリを使用して音声ファイルのメタデータを取得
            try:
                # まずmutagenが利用可能か確認
                try:
                    import mutagen
                    print(f"【デバッグ】mutagenライブラリが利用可能です")
                except ImportError:
                    # mutagenがインストールされていなければインストールを試みる
                    self.progress_updated.emit(1, "音声解析ライブラリをインストール中...")
                    print("【デバッグ】mutagenライブラリをインストールします...")
                    import subprocess
                    try:
                        subprocess.check_call(["pip", "install", "mutagen"])
                        print("【デバッグ】mutagenライブラリのインストールに成功しました")
                        import mutagen
                    except Exception as install_error:
                        print(f"【デバッグ】mutagenインストールエラー: {str(install_error)}")
                        raise ImportError("mutagenのインストールに失敗しました")
                        
                from mutagen.mp3 import MP3
                from mutagen.wave import WAVE
                from mutagen.flac import FLAC
                from mutagen.mp4 import MP4
                
                file_ext = os.path.splitext(audio_file_path)[1].lower()
                print(f"【デバッグ】ファイル拡張子: {file_ext}")
                
                # ファイルサイズを表示
                file_size = os.path.getsize(audio_file_path)
                print(f"【デバッグ】ファイルサイズ: {file_size} バイト ({file_size/1024/1024:.2f} MB)")
                
                # ファイル形式に応じて適切なクラスを使用
                if file_ext == '.mp3':
                    print(f"【デバッグ】MP3形式を検出")
                    audio = MP3(audio_file_path)
                    self.audio_duration = audio.info.length
                elif file_ext == '.wav':
                    print(f"【デバッグ】WAV形式を検出")
                    audio = WAVE(audio_file_path)
                    self.audio_duration = audio.info.length
                elif file_ext == '.flac':
                    print(f"【デバッグ】FLAC形式を検出")
                    audio = FLAC(audio_file_path)
                    self.audio_duration = audio.info.length
                elif file_ext in ['.m4a', '.mp4', '.aac']:
                    print(f"【デバッグ】M4A/MP4/AAC形式を検出")
                    audio = MP4(audio_file_path)
                    self.audio_duration = audio.info.length
                else:
                    print(f"【デバッグ】汎用フォーマットとして処理")
                    # その他の形式はgenericで試す
                    audio = mutagen.File(audio_file_path)
                    if audio and hasattr(audio.info, 'length'):
                        self.audio_duration = audio.info.length
                    else:
                        raise Exception(f"未対応の音声形式: {file_ext}")
                
                print(f"【デバッグ】mutagen使用: 音声ファイルの長さ: {self.audio_duration}秒 ({self.format_time(self.audio_duration)})")
                
            except ImportError as ie:
                # mutagenのインストールや利用ができない場合
                print(f"【デバッグ】mutagenエラー: {str(ie)}")
                raise Exception("音声ファイルの解析に必要なライブラリが利用できません")
                
        except Exception as e:
            print(f"【デバッグ】メタデータ取得エラー: {str(e)}")
            
            # 代替手段：ファイルサイズから音声長を推定
            file_size = os.path.getsize(audio_file_path)
            print(f"【デバッグ】ファイルサイズからの推定: {file_size} バイト")
            
            # ファイル拡張子を取得
            file_ext = os.path.splitext(audio_file_path)[1].lower()
            
            # 形式別のビットレート推定（バイト/秒）
            if file_ext == '.mp3':
                bytes_per_second = 16000  # 128kbps想定
                print(f"【デバッグ】MP3形式: 128kbps想定 ({bytes_per_second} バイト/秒)")
            elif file_ext == '.wav':
                bytes_per_second = 176400  # 44.1kHz, 16bit, ステレオ想定
                print(f"【デバッグ】WAV形式: 44.1kHz, 16bit, ステレオ想定 ({bytes_per_second} バイト/秒)")
            elif file_ext in ['.m4a', '.aac']:
                bytes_per_second = 20000  # 160kbps想定
                print(f"【デバッグ】M4A/AAC形式: 160kbps想定 ({bytes_per_second} バイト/秒)")
            elif file_ext == '.flac':
                bytes_per_second = 88200  # ロスレス圧縮、平均的な値
                print(f"【デバッグ】FLAC形式: ロスレス圧縮 ({bytes_per_second} バイト/秒)")
            else:
                bytes_per_second = 16000  # デフォルト値（128kbps相当）
                print(f"【デバッグ】不明な形式: デフォルト128kbps想定 ({bytes_per_second} バイト/秒)")
            
            # 推定長さ（秒）を計算
            estimated_duration = file_size / bytes_per_second
            self.audio_duration = max(estimated_duration, 60)  # 最低でも60秒と仮定
            print(f"【デバッグ】推定音声長: {self.audio_duration}秒 ({self.format_time(self.audio_duration)})")
        
        # 現在の処理位置をリセット
        self.current_timestamp = 0
        
        # 進捗計算に使用する変数を初期化
        self.last_progress_time = datetime.now()
        self.process_start_time = datetime.now()
        
        # プロジェクトディレクトリ内にlogsフォルダを作成
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(project_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        
        # 出力ディレクトリ
        self.output_directory = output_dir or os.path.join(logs_dir, "transcription_output")
        os.makedirs(self.output_directory, exist_ok=True)
        
        # 出力ファイル名を設定
        basename = os.path.basename(audio_file_path).split('.')[0]
        
        # 期待されるSRTファイル名を生成 (拡張子を除くファイル名 + .srt)
        self.expected_srt_filename = f"{basename}.srt"
        print(f"【デバッグ】期待されるSRTファイル名: {self.expected_srt_filename}")
        
        # Whisperコマンドの構築
        whisper_exe = os.path.join(self.whisper_path, "faster-whisper-xxl.exe")
        
        # QProcessを設定
        self.process = QProcess()
        self.process.setWorkingDirectory(self.whisper_path)
        
        # プロセスのバッファリングモードを設定
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        
        # シグナル接続
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.process_finished)
        
        # コマンドライン引数
        program = whisper_exe
        arguments = [
            "--language", "ja",
            "--output_dir", self.output_directory,
            "--output_format", "txt",  # 1つ目のフォーマット
            "--output_format", "srt",  # 2つ目のフォーマット
            "--verbose", "False",
            audio_file_path
        ]
        
        # デバッグ用にコマンドを出力
        print(f"実行コマンド: {program} {' '.join(arguments)}")
        
        # ログファイルのパス設定を追加
        self.log_file = os.path.join(self.output_directory, "whisper_log.txt")
        
        # ログファイルを初期化
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(f"実行コマンド: {program} {' '.join(arguments)}\n\n")
            f.write(f"開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"推定音声長: {self.audio_duration}秒\n\n")
        
        # 文字起こし実行
        self.progress_updated.emit(0, "Whisperで文字起こし実行中...")
        self.process.start(program, arguments)

        # プロセスが起動しているか確認する定期チェック
        self.process_check_timer = QTimer()
        self.process_check_timer.timeout.connect(self.check_process_status)
        self.process_check_timer.start(1000)  # 1秒ごとに状態をチェック
        
    def handle_stdout(self):
        """QProcessからの標準出力シグナルハンドラ"""
        data = self.process.readAllStandardOutput()
        if data:
            stdout = bytes(data).decode('utf-8', errors='ignore')
            print(f"プロセス出力: {stdout}")  # デバッグ用に出力内容を表示
            self.handle_stdout_data(stdout)
        
    def handle_stdout_data(self, stdout):
        """標準出力データの実際の処理"""
        # ログファイルに出力を保存
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"出力: {stdout}\n")
        
        # デバッグ出力
        print(f"【デバッグ】受信した出力: {stdout[:100]}...")
        
        # タイムスタンプが含まれている可能性が高いかどうかを判断
        contains_timestamp = "-->" in stdout or ":" in stdout
        if contains_timestamp:
            print(f"【デバッグ】タイムスタンプの可能性あり（':'または'-->'を検出）")
            
        # タイムスタンプ情報の検出を優先（この情報が最も信頼性が高い）
        if contains_timestamp and self.extract_timestamp_progress(stdout):
            # タイムスタンプが見つかったので、このイベントは処理済み
            print(f"【デバッグ】タイムスタンプ情報から進捗更新完了")
            self.segment_updated.emit(stdout)
            return
                
        # セグメント情報の検出をタイムスタンプの次に優先
        if "segment" in stdout.lower():
            print(f"【デバッグ】セグメント情報を検出")
            if self.extract_segment_info(stdout):
                # セグメント情報が見つかったので、このイベントは処理済み
                print(f"【デバッグ】セグメント情報から進捗更新完了")
                self.segment_updated.emit(stdout)
                return
            else:
                print(f"【デバッグ】セグメント情報の解析に失敗")
            
        # その他のキーワードに基づく進捗の更新
        if "Transcribing" in stdout:
            print(f"【デバッグ】キーワード「Transcribing」を検出: 進捗10%")
            self.progress_updated.emit(10, "音声を処理中...")
        elif "Detecting speakers" in stdout:
            print(f"【デバッグ】キーワード「Detecting speakers」を検出: 進捗50%")
            self.progress_updated.emit(50, "話者を検出中...")
        elif "Saving" in stdout:
            print(f"【デバッグ】キーワード「Saving」を検出: 進捗80%")
            self.progress_updated.emit(80, "文字起こし結果を保存中...")
        elif "Processing" in stdout:
            print(f"【デバッグ】キーワード「Processing」を検出: 進捗30%")
            self.progress_updated.emit(30, "音声を解析中...")
        elif "Writing" in stdout:
            print(f"【デバッグ】キーワード「Writing」を検出: 進捗90%")
            self.progress_updated.emit(90, "ファイルに出力中...")
            
        # 全ての出力をセグメント情報として送信
        self.segment_updated.emit(stdout)
        
    def handle_stderr(self):
        """エラー出力を処理"""
        data = self.process.readAllStandardError()
        if data:
            stderr = bytes(data).decode('utf-8', errors='ignore')
            print(f"Whisperエラー: {stderr}")
            
            # ログファイルにエラー出力を保存
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"エラー: {stderr}\n")
                
            # エラー出力に進捗情報が含まれる場合もあるため、標準出力と同様に処理
            self.handle_stdout_data(stderr)
    
    def process_finished(self, exit_code, exit_status):
        """プロセス終了時の処理"""
        print(f"文字起こし処理が終了しました: 終了コード={exit_code}, 終了ステータス={exit_status}")
        
        # プロセス監視タイマーを停止
        if hasattr(self, 'process_check_timer') and self.process_check_timer.isActive():
            self.process_check_timer.stop()
        
        # ログファイルに終了情報を追加
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"\n終了時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"終了コード: {exit_code}\n")
            f.write(f"終了ステータス: {exit_status}\n\n")
        
        try:
            # 出力ディレクトリが存在するか確認
            if not os.path.exists(self.output_directory):
                print(f"出力ディレクトリが見つかりません: {self.output_directory}")
                self.progress_updated.emit(100, "出力ディレクトリが見つかりません")
                self.transcription_finished.emit("", [], False)
                return
            
            # 期待されるSRTファイルのフルパスを構築
            expected_srt_path = os.path.join(self.output_directory, self.expected_srt_filename)
            print(f"【デバッグ】確認するSRTファイルパス: {expected_srt_path}")
            
            # 期待されるSRTファイルが存在するか確認
            if os.path.exists(expected_srt_path):
                print("【デバッグ】期待されるSRTファイルが見つかりました。これを解析します。")
                segments = self.parse_srt_file(expected_srt_path)
                self.segments = segments
                
                # テキスト出力 (SRTから生成または別途TXTファイルを読む)
                # まずTXTファイルを探す (SRTより優先する場合)
                expected_txt_filename = f"{os.path.splitext(self.expected_srt_filename)[0]}.txt"
                expected_txt_path = os.path.join(self.output_directory, expected_txt_filename)
                full_text = ""
                if os.path.exists(expected_txt_path):
                    print("【デバッグ】対応するTXTファイルが見つかりました。")
                    with open(expected_txt_path, 'r', encoding='utf-8') as f:
                        full_text = f.read()
                else:
                    print("【デバッグ】対応するTXTファイルが見つかりません。SRTからテキストを生成します。")
                    for segment in segments:
                        text = segment.get('text', '').strip()
                        if text:
                            full_text += f"{text}\n"
                
                self.progress_updated.emit(100, "文字起こし完了")
                self.transcription_finished.emit(full_text, segments, True)
                
            else:
                # 期待されるSRTがない場合、エラーとして処理
                error_msg = f"期待されるSRTファイルが見つかりません: {expected_srt_path}"
                print(error_msg)
                # 念のためTXTファイルだけでも存在するか確認する
                expected_txt_filename = f"{os.path.splitext(self.expected_srt_filename)[0]}.txt"
                expected_txt_path = os.path.join(self.output_directory, expected_txt_filename)
                if os.path.exists(expected_txt_path):
                    print("【デバッグ】TXTファイルは見つかりました。SRTなしで完了します。")
                    with open(expected_txt_path, 'r', encoding='utf-8') as f:
                        full_text = f.read()
                    self.progress_updated.emit(100, "文字起こし完了 (SRTなし)")
                    self.transcription_finished.emit(full_text, [], True) # セグメントは空リスト
                else:
                    self.progress_updated.emit(100, error_msg)
                    self.transcription_finished.emit("", [], False)
            
        except Exception as e:
            traceback.print_exc()
            self.progress_updated.emit(100, "結果処理中にエラーが発生しました")
            self.transcription_finished.emit("", [], False)
    
    def parse_srt_file(self, srt_file_path):
        """SRTファイルを解析してセグメントリストを生成"""
        segments = []
        
        try:
            with open(srt_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # SRTの形式: 番号、時間範囲、テキスト、空行の繰り返し
            entries = content.strip().split('\n\n')
            
            for entry in entries:
                lines = entry.split('\n')
                if len(lines) >= 3:
                    # 時間範囲の解析
                    time_range = lines[1]
                    start_time, end_time = self._parse_srt_time_range(time_range)
                    
                    # テキスト部分（3行目以降を全て結合）
                    text = ' '.join(lines[2:])
                    
                    # セグメント情報を構築
                    segment = {
                        'start': start_time,
                        'end': end_time,
                        'text': text,
                    }
                    segments.append(segment)
                    
            return segments
        except Exception as e:
            print(f"SRTファイル解析エラー: {str(e)}")
            return []
    
    def _parse_srt_time_range(self, time_range):
        """SRTの時間範囲文字列をパース"""
        try:
            start_str, end_str = time_range.split(' --> ')
            
            # 時間文字列をパース (時:分:秒,ミリ秒)
            def parse_time(time_str):
                hours, minutes, seconds = time_str.replace(',', '.').split(':')
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                
            return parse_time(start_str), parse_time(end_str)
        except Exception as e:
            print(f"時間範囲パースエラー: {str(e)}")
            return 0, 0

    def get_segment_by_time(self, time_seconds):
        """
        指定した時間に該当するセグメントを取得する
        
        Args:
            time_seconds (float): 秒単位の時間
            
        Returns:
            dict: セグメント情報
        """
        for segment in self.segments:
            start = segment.get('start', 0)
            end = segment.get('end', 0)
            if start <= time_seconds <= end:
                return segment
        return None 

    def extract_segment_info(self, stdout):
        """出力からセグメント情報を抽出して進捗を更新"""
        # "Processing segment X / Y"のようなフォーマットを探す
        if "segment" in stdout.lower():
            try:
                parts = stdout.split("segment")[1].strip().split("/")
                if len(parts) == 2:
                    current = int(parts[0].strip())
                    total = int(parts[1].strip().split()[0])
                    
                    self.current_segment = current
                    self.total_segments = total
                    
                    # 進捗率を計算 (0%〜99%の範囲で)
                    progress = int(99 * current / total)
                    print(f"【デバッグ】セグメント進捗計算: current={current}, total={total}, progress={progress}%")
                    self.progress_updated.emit(progress, f"文字起こし中... セグメント {current}/{total}")
                    return True
            except Exception as e:
                print(f"セグメント情報抽出エラー: {str(e)}")
        
        return False

    def extract_timestamp_progress(self, stdout):
        """Whisperの出力からタイムスタンプを抽出して進捗状況を更新する"""
        if not stdout or len(stdout.strip()) == 0:
            return False

        cleaned_stdout = stdout.strip()
        # print(f"[DBG_TSP] Input: {cleaned_stdout[:100]}...") # 入力ログ

        # 正規表現パターン (前回修正済み)
        patterns = [
            r"\[(\d+):(\d+)\.(\d+)\s+-->\s+(\d+):(\d+)\.(\d+)\]", # [MM:SS.ms --> MM:SS.ms]
            r"(\d+):(\d+):(\d+),(\d+)\s+-->\s+(\d+):(\d+):(\d+),(\d+)", # HH:MM:SS,ms --> HH:MM:SS,ms
            r"(\d+):(\d+):(\d+)\.(\d+)\s+-->\s+(\d+):(\d+):(\d+)\.(\d+)", # HH:MM:SS.ms --> HH:MM:SS.ms
            r"(\d+):(\d+):(\d+)\s+-->\s+(\d+):(\d+):(\d+)", # HH:MM:SS --> HH:MM:SS
        ]

        latest_timestamp_sec = -1

        for line_num, line in enumerate(cleaned_stdout.splitlines()):
            # print(f"[DBG_TSP] Processing line {line_num}: {line[:80]}...") # 行処理ログ
            matched_in_line = False
            for idx, pattern in enumerate(patterns):
                # print(f"[DBG_TSP] Trying pattern {idx}...") # パターン試行ログ
                match = re.search(pattern, line)
                if match:
                    print(f"[DBG_TSP] Pattern {idx} MATCHED on line {line_num}: {line}") # マッチログ
                    try:
                        groups = match.groups()
                        print(f"[DBG_TSP]   Groups: {groups}") # グループログ
                        end_sec = 0
                        # ... (パターンに応じた終了時間の計算 - 変更なし)
                        if idx == 0: # [MM:SS.ms --> MM:SS.ms]
                            end_min, end_s, end_ms = int(groups[3]), int(groups[4]), int(groups[5])
                            end_sec = end_min * 60 + end_s + end_ms / 1000.0
                        elif idx == 1: # HH:MM:SS,ms --> HH:MM:SS,ms
                            end_h, end_min, end_s, end_ms = int(groups[4]), int(groups[5]), int(groups[6]), int(groups[7])
                            end_sec = end_h * 3600 + end_min * 60 + end_s + end_ms / 1000.0
                        elif idx == 2: # HH:MM:SS.ms --> HH:MM:SS.ms
                            end_h, end_min, end_s, end_ms = int(groups[4]), int(groups[5]), int(groups[6]), int(groups[7])
                            end_sec = end_h * 3600 + end_min * 60 + end_s + end_ms / 1000.0
                        elif idx == 3: # HH:MM:SS --> HH:MM:SS
                            end_h, end_min, end_s = int(groups[3]), int(groups[4]), int(groups[5])
                            end_sec = end_h * 3600 + end_min * 60 + end_s
                        
                        print(f"[DBG_TSP]   Calculated end_sec: {end_sec:.3f}") # 計算結果ログ
                        latest_timestamp_sec = max(latest_timestamp_sec, end_sec)
                        matched_in_line = True
                        break # マッチしたので次の行へ
                    except Exception as e:
                        print(f"[DBG_TSP]   Timestamp parsing ERROR (Pattern {idx}): {e} - Match: {groups}")
                        continue
                # else: print(f"[DBG_TSP] Pattern {idx} NO MATCH") # マッチしなかった場合

        # 全行チェック後の進捗計算とシグナル発行
        # print(f"[DBG_TSP] Final latest_timestamp_sec: {latest_timestamp_sec:.3f}, Audio duration: {self.audio_duration:.3f}")
        if latest_timestamp_sec >= 0 and self.audio_duration > 0:
            # 現在の進捗率を計算
            current_progress = int((latest_timestamp_sec / self.audio_duration) * 100)
            # 表示する進捗率（前回より減らないように、99%上限）
            display_progress = max(self.last_progress_percent, min(current_progress, 99))
            # print(f"[DBG_TSP] Calculated progress: {current_progress}%, Display progress: {display_progress}%, Last progress: {self.last_progress_percent}%")

            # 前回より進捗率（整数）が増えた場合のみ last_progress_percent を更新
            if display_progress > self.last_progress_percent:
                self.last_progress_percent = display_progress

            # === 変更点: タイムスタンプが見つかれば必ずシグナルを発行 ===
            duration_str = self.format_time(self.audio_duration)
            time_str = self.format_time(latest_timestamp_sec)
            print(f"【進捗シグナル発行】現在位置: {latest_timestamp_sec:.2f}秒 / {self.audio_duration:.2f}秒 ({display_progress}%)")
            self.progress_updated.emit(display_progress, f"文字起こし中... {time_str}/{duration_str} ({display_progress}%)")
            return True # タイムスタンプ処理完了

        elif latest_timestamp_sec >= 0:
             print("[DBG_TSP] Timestamp found but audio duration is zero or negative.")
             return True # タイムスタンプは見つかった

        # print("[DBG_TSP] No timestamp patterns matched in the input.")
        return False
        
    def format_time(self, seconds):
        """秒数を [HH:]MM:SS 形式にフォーマット"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    def check_process_status(self):
        """プロセスの状態をチェック"""
        current_state = self.process.state()
        print(f"【デバッグ】プロセス状態チェック: state={current_state}（0=未実行、1=開始中、2=実行中）")
        
        if current_state == QProcess.NotRunning:
            # プロセスが実行されていない場合
            print(f"【デバッグ】プロセスが実行されていません。終了処理を実行します。")
            self.process_check_timer.stop()
            self.process_finished(self.process.exitCode(), self.process.exitStatus())
        else:
            # プロセスが実行中の場合のみ、状態を確認
            print(f"【デバッグ】プロセスが実行中です: audio_duration={self.audio_duration}秒, current_timestamp={self.current_timestamp}秒")
            
            # 最低でも5秒に1回は進捗情報を更新
            time_since_last_update = (datetime.now() - self.last_progress_time).total_seconds()
            print(f"【デバッグ】最終更新からの経過時間: {time_since_last_update}秒")
            
            if time_since_last_update >= 5:
                self.last_progress_time = datetime.now()
                elapsed_seconds = (datetime.now() - self.process_start_time).total_seconds()
                print(f"【デバッグ】プロセス開始からの経過時間: {elapsed_seconds}秒")
                
                # 現在のタイムスタンプ情報から進捗メッセージを表示
                if self.current_timestamp > 0:
                    time_str = self.format_time(self.current_timestamp)
                    print(f"【デバッグ】現在のタイムスタンプ: {time_str}")
                    
                    if self.audio_duration > 0:
                        duration_str = self.format_time(self.audio_duration)
                        progress_percent = min(int((self.current_timestamp / self.audio_duration) * 100), 99)
                        self.last_progress_percent = progress_percent  # 進捗率を保存
                        print(f"【デバッグ】進捗計算: {self.current_timestamp}/{self.audio_duration} = {progress_percent}%")
                        self.progress_updated.emit(progress_percent, f"文字起こし中... {time_str}/{duration_str} ({progress_percent}%)")
                    else:
                        # 音声長が不明な場合は前回の進捗率を維持
                        print(f"【デバッグ】音声長不明のため進捗率を前回値に維持")
                        self.progress_updated.emit(self.last_progress_percent, f"文字起こし中... 現在位置 {time_str}")
                else:
                    # タイムスタンプが検出されていない場合は経過時間のみ表示（進捗率は更新しない）
                    elapsed_time_str = self.format_time(elapsed_seconds)
                    if self.last_progress_percent > 0:
                        # 過去に進捗があれば、それを維持
                        print(f"【デバッグ】タイムスタンプ未検出だが前回の進捗率を維持: {self.last_progress_percent}%")
                        self.progress_updated.emit(self.last_progress_percent, f"文字起こし中... (経過時間 {elapsed_time_str})")
                    else:
                        # 初期状態では進捗率は更新せず、経過時間のみ表示
                        print(f"【デバッグ】タイムスタンプ未検出：進捗率更新なし")
                        self.progress_updated.emit(0, f"文字起こしを実行中... 経過時間 {elapsed_time_str}")
            
            # プロセスが実行中の場合、出力ディレクトリ内のファイルを確認
            try:
                if os.path.exists(self.output_directory):
                    # ログファイルのサイズを確認
                    if os.path.exists(self.log_file):
                        log_size = os.path.getsize(self.log_file)
                        # ログファイルが大きくなっていれば処理が進行中と判断
                        if hasattr(self, 'last_log_size'):
                            size_diff = log_size - self.last_log_size
                            print(f"【デバッグ】ログファイルサイズ変化: {self.last_log_size} -> {log_size} ({size_diff:+d} bytes)")
                            if size_diff > 0:
                                print(f"【デバッグ】ログファイルが更新されています: {log_size} bytes")
                        else:
                            print(f"【デバッグ】ログファイルサイズ初期値: {log_size} bytes")
                        self.last_log_size = log_size
                
                # 外部コマンドでプロセスが実行中か確認（Windowsの場合）
                if os.name == 'nt' and (datetime.now() - self.process_start_time).total_seconds() > 30:
                    # プロセス開始から30秒以上経過している場合のみチェック
                    import subprocess
                    try:
                        # tasklist実行
                        print(f"【デバッグ】tasklist実行でプロセス確認を試行")
                        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq faster-whisper-xxl.exe'], 
                                              capture_output=True, text=True, check=False)
                        if 'faster-whisper-xxl.exe' not in result.stdout:
                            print(f"【デバッグ】faster-whisper-xxl.exeプロセスが見つかりません")
                            # プロセスが存在しない場合でも正常に動作している場合があるため、エラーにはしない
                        else:
                            print(f"【デバッグ】faster-whisper-xxl.exeプロセスが実行中です")
                    except Exception as e:
                        print(f"【デバッグ】プロセス確認エラー: {str(e)}")
            except Exception as e:
                print(f"【デバッグ】プロセス状態確認エラー: {str(e)}")

# トランスクリプションスレッドクラスを追加
class TranscriptionThread(QThread):
    """文字起こしを実行するスレッド"""
    
    # シグナルの定義
    finished = pyqtSignal(str, list, bool)  # (文字起こし結果, セグメント, 成功フラグ)
    progress = pyqtSignal(int, str)  # (進捗値, メッセージ)
    
    def __init__(self, whisper_path, audio_file_path, output_dir, diarize=True):
        super().__init__()
        self.whisper_path = whisper_path
        self.audio_file_path = audio_file_path
        self.output_dir = output_dir
        self.diarize = diarize
    
    def run(self):
        """スレッドで実行される処理"""
        try:
            import os
            import subprocess
            import tempfile
            
            self.progress.emit(10, "文字起こしの準備中...")
            
            # 一時ディレクトリを作成
            temp_dir = self.output_dir or tempfile.mkdtemp()
            os.makedirs(temp_dir, exist_ok=True)
            
            # 出力ファイル名を設定
            basename = os.path.basename(self.audio_file_path).split('.')[0]
            output_srt = os.path.join(temp_dir, f"{basename}.srt")
            output_txt = os.path.join(temp_dir, f"{basename}.txt")
            
            # Whisperコマンドの構築
            whisper_exe = os.path.join(self.whisper_path, "faster-whisper-xxl.exe")
            
            # コマンドライン引数
            cmd = [
                whisper_exe,
                "--language", "ja",
                "--output_dir", temp_dir,
                "--output_format", "txt",  # 1つ目のフォーマット
                "--output_format", "srt",  # 2つ目のフォーマット
                "--verbose", "False",
                self.audio_file_path
            ]
            
            # デバッグ用にコマンドを出力
            print(f"実行コマンド: {' '.join(cmd)}")
            
            # Whisper実行
            self.progress.emit(20, "Whisperで文字起こし実行中...")
            
            # サブプロセスで実行
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            
            # 実行結果を出力
            print(f"標準出力:\n{process.stdout}")
            print(f"エラー出力:\n{process.stderr}")
            print(f"終了コード: {process.returncode}")
            
            # エラーチェック
            if process.returncode != 0:
                self.progress.emit(100, f"文字起こしに失敗しました")
                self.finished.emit("", [], False)
                return
            
            self.progress.emit(70, "文字起こし結果を処理中...")
            
            # 出力ディレクトリ内のファイルをリスト
            output_files = os.listdir(temp_dir)
            print(f"出力ディレクトリ内ファイル: {', '.join(output_files)}")
            
            # SRTファイルを探す
            srt_files = [f for f in output_files if f.endswith(".srt")]
            if srt_files:
                srt_file = os.path.join(temp_dir, srt_files[0])
                
                # SRTファイルを解析（実装は親クラスに依存するので、ここでは簡易版）
                segments = []
                full_text = ""
                
                with open(srt_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # SRTの形式: 番号、時間範囲、テキスト、空行の繰り返し
                entries = content.strip().split('\n\n')
                
                for entry in entries:
                    lines = entry.split('\n')
                    if len(lines) >= 3:
                        # 時間範囲の解析
                        time_range = lines[1]
                        start_time, end_time = self._parse_srt_time_range(time_range)
                        
                        # テキスト部分（3行目以降を全て結合）
                        text = ' '.join(lines[2:])
                        
                        # セグメント情報を構築
                        segment = {
                            'start': start_time,
                            'end': end_time,
                            'text': text,
                        }
                        segments.append(segment)
                        full_text += f"{text}\n"
                
                self.progress.emit(90, "文字起こし完了")
                self.finished.emit(full_text, segments, True)
                
            elif os.path.exists(output_txt):
                # テキストファイルから読み込み
                with open(output_txt, 'r', encoding='utf-8') as f:
                    full_text = f.read()
                
                self.progress.emit(90, "文字起こし完了（話者分離なし）")
                self.finished.emit(full_text, [], True)
                
            else:
                self.progress.emit(100, "文字起こし出力ファイルが見つかりません")
                self.finished.emit("", [], False)
                
        except Exception as e:
            traceback.print_exc()
            self.progress.emit(100, f"エラー: {str(e)}")
            self.finished.emit("", [], False)
    
    def _parse_srt_time_range(self, time_range):
        """SRTの時間範囲文字列をパース"""
        try:
            start_str, end_str = time_range.split(' --> ')
            
            # 時間文字列をパース (時:分:秒,ミリ秒)
            def parse_time(time_str):
                hours, minutes, seconds = time_str.replace(',', '.').split(':')
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                
            return parse_time(start_str), parse_time(end_str)
        except Exception as e:
            print(f"時間範囲パースエラー: {str(e)}")
            return 0, 0 