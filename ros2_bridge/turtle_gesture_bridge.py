#!/usr/bin/env python3
"""
turtle_gesture_bridge.py  (WSL2 / ROS2 Jazzy 側で動かす)

Windows側の gesture_control.py が書き出す osouji_cmd.txt を読み、
  GO   -> 前進 / STOP -> 停止  を cmd_vel に流す。

送り先は引数で切り替え:
  python3 turtle_gesture_bridge.py            # turtlesim (Twist, /turtle1/cmd_vel)
  python3 turtle_gesture_bridge.py tb3        # TurtleBot3 Gazebo (TwistStamped, /cmd_vel)

※ 新しいGazebo(Jazzy)では /cmd_vel の型が TwistStamped。turtlesim は従来の Twist。
"""
import sys
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TwistStamped

CMD_FILE = "/mnt/c/Users/30day/osouji_cmd.txt"

TARGETS = {
    "turtlesim": {"topic": "/turtle1/cmd_vel", "speed": 2.0, "stamped": False},
    "tb3":       {"topic": "/cmd_vel",         "speed": 0.2, "stamped": True},
}


class GestureBridge(Node):
    def __init__(self, target):
        super().__init__("gesture_bridge")
        cfg = TARGETS.get(target, TARGETS["turtlesim"])
        self.topic = cfg["topic"]
        self.speed = cfg["speed"]
        self.stamped = cfg["stamped"]
        msg_type = TwistStamped if self.stamped else Twist
        self.pub = self.create_publisher(msg_type, self.topic, 10)
        self.timer = self.create_timer(0.1, self.tick)  # 10Hz
        self.last = None
        self.get_logger().info(
            f"ブリッジ開始。送り先={self.topic} 型={msg_type.__name__} 速度={self.speed}。"
            "パーで前進 / グーで停止。Ctrl+Cで終了。"
        )

    def read_cmd(self):
        try:
            with open(CMD_FILE, encoding="utf-8") as f:
                return f.read().strip()
        except (FileNotFoundError, OSError):
            return ""

    def tick(self):
        cmd = self.read_cmd()
        vx, wz = 0.0, 0.0
        turn = 0.8
        if cmd == "GO":
            vx = self.speed
        elif cmd == "BACK":
            vx = -self.speed
        elif cmd == "LEFT":
            wz = turn
        elif cmd == "RIGHT":
            wz = -turn
        # STOP / 空 は vx=wz=0(停止)
        if self.stamped:
            msg = TwistStamped()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.twist.linear.x = vx
            msg.twist.angular.z = wz
        else:
            msg = Twist()
            msg.linear.x = vx
            msg.angular.z = wz
        self.pub.publish(msg)
        if cmd != self.last:
            self.get_logger().info(f"command = {cmd or '(なし)'}")
            self.last = cmd


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "turtlesim"
    rclpy.init()
    node = GestureBridge(target)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
