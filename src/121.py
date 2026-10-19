from __future__ import annotations

import logging
import math

from lerobot.lerobot_types import RobotAction, RobotObservation
from lerobot.robots.robot import AngleUnit, Robot
from lerobot.utils.decorators import (
    check_if_already_connected,
    check_if_not_connected,
)
from lerobot.utils.errors import (
    DeviceAlreadyConnectedError,
    DeviceNotConnectedError,
)

from .config_realman import RealmanConfig
from .sdk_driver import RealmanSdkDriver


logger = logging.getLogger(__name__)


def _clamp(
    value: float,
    lower: float,
    upper: float,
) -> float:
    return max(lower, min(upper, value))


class Realman(Robot):
    """LeRobot 六轴 RealMan 机械臂实现，角度单位为度。"""

    config_class = RealmanConfig
    name = "realman"
    _angle_unit = AngleUnit.DEGREES

    def __init__(self, config: RealmanConfig):
        super().__init__(config)

        self.config = config
        self._joint_names = config.joint_names

        self._sdk = RealmanSdkDriver(
            ip=config.ip,
            port=config.port,
            log_level=config.log_level,
            thread_mode=config.thread_mode,
        )

        self._last_action: dict[str, float] = {}

    @property
    def observation_features(self) -> dict[str, type]:
        return {
            f"{name}.pos": float
            for name in self._joint_names
        }

    @property
    def action_features(self) -> dict[str, type]:
        return {
            f"{name}.pos": float
            for name in self._joint_names
        }

    @property
    def is_connected(self) -> bool:
        return self._sdk.is_connected

    @property
    def has_connection_resources(self) -> bool:
        return self._sdk.has_resources

    @property
    def is_calibrated(self) -> bool:
        return True

    def arm_joint_names(self) -> tuple[str, ...]:
        return self._joint_names

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:
        if self.has_connection_resources:
            raise DeviceAlreadyConnectedError(
                "RealMan 仍持有 SDK 资源，请先 disconnect()"
            )

        logger.info(
            "正在连接 RealMan: %s:%s",
            self.config.ip,
            self.config.port,
        )

        self._sdk.connect()

        try:
            self._sdk.clear_joint_errors()
            self._last_action = self._read_joint_positions()

            logger.info("RealMan 连接成功")

        except Exception:
            try:
                self._sdk.disconnect()
            finally:
                self._last_action = {}

            raise

    def disconnect(self) -> None:
        if not self.has_connection_resources:
            return

        try:
            self._sdk.disconnect()
        finally:
            self._last_action = {}

        logger.info("RealMan 已断开")

    def calibrate(self) -> None:
        # RealMan 校准由控制器或示教器完成。
        return

    def configure(self) -> None:
        # SDK 基础配置在 connect() 中完成。
        return

    def _read_joint_positions(self) -> dict[str, float]:
        values = self._sdk.get_joint_degrees()

        if len(values) != len(self._joint_names):
            raise RuntimeError(
                f"读取到 {len(values)} 个关节角，"
                f"预期 {len(self._joint_names)} 个"
            )

        return {
            f"{name}.pos": float(value)
            for name, value in zip(
                self._joint_names,
                values,
                strict=True,
            )
        }

    @check_if_not_connected
    def get_observation(self) -> RobotObservation:
        return self._read_joint_positions()

    def _validate_action(
        self,
        action: RobotAction,
    ) -> None:
        required = {
            f"{name}.pos"
            for name in self._joint_names
        }

        actual = set(action)
        unknown = actual - required
        missing = required - actual

        if unknown:
            raise ValueError(
                f"动作包含未知字段: {sorted(unknown)}"
            )

        if missing:
            raise ValueError(
                f"动作缺少字段: {sorted(missing)}"
            )

        for key, value in action.items():
            value = float(value)

            if not math.isfinite(value):
                raise ValueError(
                    f"{key} 必须是有限数，收到 {value}"
                )

    def _clip_action(
        self,
        action: RobotAction,
    ) -> dict[str, float]:
        self._validate_action(action)

        result: dict[str, float] = {}

        for index, name in enumerate(self._joint_names):
            key = f"{name}.pos"
            value = float(action[key])

            lower = self.config.joint_min_deg[index]
            upper = self.config.joint_max_deg[index]

            if self.config.enable_joint_limit:
                if not lower <= value <= upper:
                    if not self.config.clip_out_of_range_action:
                        raise ValueError(
                            f"{key}={value} 超出安全范围 "
                            f"[{lower}, {upper}]"
                        )

                    logger.warning(
                        "%s=%s 超出范围 [%s, %s]，已自动裁剪",
                        key,
                        value,
                        lower,
                        upper,
                    )

                value = _clamp(value, lower, upper)

            result[key] = value

        return result

    @check_if_not_connected
    def send_action(
        self,
        action: RobotAction,
    ) -> RobotAction:
        accepted = self._clip_action(action)

        joint_degrees = [
            accepted[f"{name}.pos"]
            for name in self._joint_names
        ]

        if self.config.move_mode == "movej":
            self._sdk.movej(
                joint_degrees=joint_degrees,
                velocity=self.config.movej_velocity,
                radius=self.config.movej_radius,
                connect=0,
                block=self.config.movej_block,
            )

        elif self.config.move_mode == "canfd":
            self._sdk.movej_canfd(
                joint_degrees=joint_degrees,
                follow=self.config.canfd_follow,
                trajectory_mode=self.config.canfd_trajectory_mode,
                radio=self.config.canfd_radio,
            )

        else:
            raise ValueError(
                f"未知 move_mode: {self.config.move_mode}"
            )

        self._last_action = dict(accepted)
        return dict(accepted)

    def latest_action(self) -> RobotAction:
        return dict(self._last_action)