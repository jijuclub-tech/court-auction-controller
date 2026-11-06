"""시험용 관제기 실행: 저장 경로만 시험 폴더로 바꿔 web_ui를 띄움.  사용: python3 run_controller.py <port> <save_dir>"""
import sys, os
sys.path.insert(0, os.getcwd())
import controller
oi = controller.AuctionController.__init__
controller.AuctionController.__init__ = lambda self, db_path="controller.db", base_save_path=None: oi(self, db_path, base_save_path or sys.argv[2])
import web_ui
web_ui.app.run(host="127.0.0.1", port=int(sys.argv[1]))
