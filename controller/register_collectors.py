"""jiju0001~jiju0009 관제형 수집기 등록"""
from controller import AuctionController

ctrl = AuctionController()
for i in range(1, 10):
    name = f"jiju{i:04d}"
    ctrl.register_collector(name, "control")
    print(f"Registered: {name} (control)")
print("Done.")
