"""mobile-use — direct mobile device control via Appium.

iOS (XCUITest) and Android (UIAutomator2) from one harness.
"""
__version__ = "0.1.0"

from mobile_use.collector import Collector
from mobile_use.multibox import Device, DevicePool

__all__ = ["Collector", "Device", "DevicePool", "__version__"]
