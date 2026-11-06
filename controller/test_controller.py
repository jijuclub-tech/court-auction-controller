"""간단한 테스트 (DB 없이)"""
from controller import AuctionController, TaskType

ctrl = AuctionController(base_save_path="/tmp/auction_test")
ctrl.register_collector("테스트수집기", "control")
ctrl.ensure_case("2026타경99999", "대전지방법원")
ctrl.create_task("2026타경99999", TaskType.APPRAISAL, 9)
ctrl.plan_next_hour()
print(ctrl.get_status_summary())
instr = ctrl.get_next_instruction("테스트수집기")
print("지시서 save_path:", instr.save_path if instr else None)
