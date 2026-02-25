#!/usr/bin/env python3
"""
Sentient Core - Vision Detection Service (Jetson Local)

Jetson Orin Nano + IMX219 CSI cameras
Inference: YOLOv8n via ONNX Runtime (CPU)
Camera: IMX219 on CSI port 1 (sensor-id=1) via nvarguscamerasrc

Features:
  - YOLOv8n inference via ONNX Runtime
  - GStreamer camera capture with nvarguscamerasrc
  - MQTT detection publishing to perception service
  - Time-based MQTT throttle (500ms min interval)
  - MJPEG stream with bounding box overlays on port 8091
"""

import json
import time
import signal
import logging
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from mjpeg_server import MJPEGServer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CAMERA_SENSOR_ID = 1  # IMX219 on CSI port 1 (sensor-id=0 segfaults)
CAMERA_SIZE = (640, 480)
MODEL_INPUT_SIZE = (640, 640)
MODEL_PATH = "/opt/sentient-core/models/yolov8n.onnx"

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_USER = "sentient"
MQTT_PASS = "sentient1312"
CAMERA_ID = "jetson"
LOCATION = "desk"

CONFIDENCE_THRESHOLD = 0.3
MQTT_THROTTLE_S = 0.5
MJPEG_PORT = 8091
MJPEG_FPS = 10

# COCO class names (80)
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush"
]

BOX_COLORS = [
    (0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 0),
    (255, 0, 255), (0, 255, 255), (128, 255, 0), (255, 128, 0),
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('vision-jetson')

# ---------------------------------------------------------------------------
# GStreamer camera capture
# ---------------------------------------------------------------------------

def build_gstreamer_pipeline(sensor_id, width, height, fps=30):
    """Build nvarguscamerasrc GStreamer pipeline string for OpenCV."""
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} "
        f"! video/x-raw(memory:NVMM),width={width},height={height},"
        f"framerate={fps}/1 "
        f"! nvvidconv ! video/x-raw,format=BGRx "
        f"! videoconvert ! video/x-raw,format=BGR "
        f"! appsink drop=1"
    )

# ---------------------------------------------------------------------------
# YOLOv8 ONNX inference
# ---------------------------------------------------------------------------

class YOLOv8Detector:
    """YOLOv8 object detector using ONNX Runtime."""

    def __init__(self, model_path, conf_threshold=0.3):
        self.conf_threshold = conf_threshold
        self.nms_threshold = 0.45

        logger.info(f"Loading ONNX model: {model_path}")
        self.session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        input_shape = self.session.get_inputs()[0].shape
        self.model_h = input_shape[2]
        self.model_w = input_shape[3]
        logger.info(f"Model loaded: input={self.model_w}x{self.model_h}")

        # Preallocate preprocessing buffer — avoids per-frame allocation
        self._input_buffer = np.zeros((1, 3, self.model_h, self.model_w), dtype=np.float32)
        # Letterbox state (set each detect() call, used for bbox correction)
        self._lb_scale = 1.0
        self._lb_pad_x = 0
        self._lb_pad_y = 0

    def detect(self, frame):
        """Run detection on a BGR frame. Returns list of detection dicts."""
        h, w = frame.shape[:2]

        # Letterbox: scale to fit within model input preserving aspect ratio,
        # then pad with gray (114) to reach exact model dimensions.
        scale = min(self.model_w / w, self.model_h / h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        pad_x = (self.model_w - new_w) // 2
        pad_y = (self.model_h - new_h) // 2

        # Store for bbox correction
        self._lb_scale = scale
        self._lb_pad_x = pad_x
        self._lb_pad_y = pad_y

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Write into preallocated buffer (fill gray, then copy resized region)
        self._input_buffer[:] = 114.0 / 255.0
        # resized is HWC BGR; convert to CHW float and place into padded region
        region = resized.astype(np.float32) / 255.0  # (new_h, new_w, 3)
        region_chw = region.transpose(2, 0, 1)        # (3, new_h, new_w)
        self._input_buffer[0, :, pad_y:pad_y + new_h, pad_x:pad_x + new_w] = region_chw
        blob = self._input_buffer

        # Inference
        outputs = self.session.run(None, {self.input_name: blob})

        # Parse YOLOv8 output: [1, 84, 8400] -> [8400, 84]
        out = outputs[0][0].T
        boxes = out[:, :4]      # cx, cy, w, h (in model coords)
        scores = out[:, 4:]     # 80 class scores

        # Filter by confidence
        max_scores = np.max(scores, axis=1)
        mask = max_scores > self.conf_threshold
        filtered_boxes = boxes[mask]
        filtered_scores = scores[mask]
        filtered_max = max_scores[mask]
        class_ids = np.argmax(filtered_scores, axis=1)

        if len(filtered_boxes) == 0:
            return []

        # Convert cx,cy,w,h to x,y,w,h for NMS
        x1 = filtered_boxes[:, 0] - filtered_boxes[:, 2] / 2
        y1 = filtered_boxes[:, 1] - filtered_boxes[:, 3] / 2
        bw = filtered_boxes[:, 2]
        bh = filtered_boxes[:, 3]
        nms_boxes = np.stack([x1, y1, bw, bh], axis=1).tolist()

        indices = cv2.dnn.NMSBoxes(
            nms_boxes, filtered_max.tolist(),
            self.conf_threshold, self.nms_threshold
        )

        if len(indices) == 0:
            return []

        indices = np.array(indices).flatten()
        detections = []

        for idx in indices:
            cx, cy, dw, dh = filtered_boxes[idx]
            cls = int(class_ids[idx])
            conf = float(filtered_max[idx])

            # Undo letterbox: subtract padding, divide by scale to get original-space coords
            x1 = (float(cx - dw / 2) - self._lb_pad_x) / self._lb_scale
            y1 = (float(cy - dh / 2) - self._lb_pad_y) / self._lb_scale
            x2 = (float(cx + dw / 2) - self._lb_pad_x) / self._lb_scale
            y2 = (float(cy + dh / 2) - self._lb_pad_y) / self._lb_scale

            detections.append({
                "class": COCO_CLASSES[cls] if cls < 80 else f"class_{cls}",
                "class_id": cls,
                "confidence": round(conf, 3),
                "bbox": {
                    "x_min": max(0, round(x1)),
                    "y_min": max(0, round(y1)),
                    "x_max": min(w, round(x2)),
                    "y_max": min(h, round(y2)),
                }
            })

        return detections

# ---------------------------------------------------------------------------
# Vision service
# ---------------------------------------------------------------------------

class JetsonVisionDetector:
    def __init__(self):
        self.running = True
        self.mqtt_client = None
        self.mjpeg_server = None
        self.last_publish_time = 0
        self.last_classes = set()
        self.frame_count = 0

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        self.running = False

    def connect_mqtt(self):
        import paho.mqtt.client as mqtt
        self.mqtt_client = mqtt.Client(client_id=f"vision-{CAMERA_ID}")
        self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.mqtt_client.on_connect = lambda c, u, f, rc: logger.info(f"MQTT connected (rc={rc})")
        self.mqtt_client.on_disconnect = lambda c, u, rc: logger.warning(f"MQTT disconnected (rc={rc})")

        try:
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
            logger.info(f"MQTT connecting to {MQTT_BROKER}:{MQTT_PORT}")
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            self.mqtt_client = None

    def start_mjpeg(self):
        try:
            self.mjpeg_server = MJPEGServer(port=MJPEG_PORT, target_fps=MJPEG_FPS)
            self.mjpeg_server.start()
            logger.info(f"MJPEG streaming on port {MJPEG_PORT}")
        except Exception as e:
            logger.warning(f"MJPEG server failed to start: {e}")
            self.mjpeg_server = None

    def draw_detections(self, frame, detections):
        """Draw bounding boxes and labels on frame."""
        for det in detections:
            bbox = det["bbox"]
            x1, y1 = int(bbox["x_min"]), int(bbox["y_min"])
            x2, y2 = int(bbox["x_max"]), int(bbox["y_max"])
            color = BOX_COLORS[det["class_id"] % len(BOX_COLORS)]
            label = f'{det["class"]} {det["confidence"]:.0%}'

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

        info = f"Jetson | ONNX-CPU | {LOCATION} | {len(detections)} obj"
        cv2.putText(frame, info, (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
        ts = datetime.now().strftime("%H:%M:%S")
        cv2.putText(frame, ts, (frame.shape[1] - 75, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
        return frame

    def publish_detections(self, detections, fps=0.0):
        """Publish detections via MQTT with throttling.

        Always enforces the minimum interval — no burst on confidence flicker.
        """
        if not self.mqtt_client:
            return

        now = time.time()
        if (now - self.last_publish_time) >= MQTT_THROTTLE_S:
            current_classes = {d["class"] for d in detections}
            payload = {
                "camera_id": CAMERA_ID,
                "location": LOCATION,
                "timestamp": datetime.now().isoformat(),
                "objects": detections,
                "frame_count": self.frame_count,
                "fps": round(fps, 1),
            }
            topic = f"sentient/sensor/vision/{CAMERA_ID}/detection"
            self.mqtt_client.publish(topic, json.dumps(payload), qos=0)
            self.last_publish_time = now
            self.last_classes = current_classes

    def run(self):
        """Main detection loop."""
        logger.info("Starting Jetson vision detector...")

        # Load model
        if not Path(MODEL_PATH).exists():
            logger.error(f"Model not found: {MODEL_PATH}")
            logger.info("Download with: huggingface_hub.hf_hub_download('salim4n/yolov8n-detect-onnx', ...)")
            return

        detector = YOLOv8Detector(MODEL_PATH, CONFIDENCE_THRESHOLD)

        # Connect services
        self.connect_mqtt()
        self.start_mjpeg()

        # Open camera
        gst_pipeline = build_gstreamer_pipeline(
            CAMERA_SENSOR_ID,
            CAMERA_SIZE[0], CAMERA_SIZE[1]
        )
        logger.info(f"Opening camera sensor-id={CAMERA_SENSOR_ID}")
        cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)

        if not cap.isOpened():
            logger.error("Failed to open camera!")
            return

        logger.info(f"Camera open: {CAMERA_SIZE[0]}x{CAMERA_SIZE[1]}")
        time.sleep(1)  # Camera warm-up

        fps_time = time.time()
        fps_frames = 0
        fail_count = 0
        MAX_CONSECUTIVE_FAILS = 30  # ~3 seconds of failures triggers restart
        PIPELINE_RESTART_FRAMES = 2000  # Restart camera every 2K frames (~10min) to prevent Argus DMA leak
        pipeline_frame_count = 0

        while self.running:
            try:
                ret, frame = cap.read()
                if not ret:
                    fail_count += 1
                    if fail_count >= MAX_CONSECUTIVE_FAILS:
                        logger.warning(f"Camera failed {fail_count} times, restarting pipeline...")
                        cap.release()
                        time.sleep(3)
                        cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
                        if not cap.isOpened():
                            logger.error("Camera restart failed, waiting 10s...")
                            time.sleep(10)
                            cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
                        fail_count = 0
                        time.sleep(1)
                    else:
                        time.sleep(0.1)
                    continue

                fail_count = 0
                self.frame_count += 1
                fps_frames += 1
                pipeline_frame_count += 1

                # Periodic pipeline restart to prevent Argus DMA memory leak
                if pipeline_frame_count >= PIPELINE_RESTART_FRAMES:
                    logger.info(f"Preventive camera restart at {self.frame_count} frames")
                    cap.release()
                    time.sleep(2)
                    cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
                    if not cap.isOpened():
                        logger.error("Camera restart failed!")
                        time.sleep(5)
                        cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
                    pipeline_frame_count = 0
                    time.sleep(1)
                    continue

                # Run detection
                detections = detector.detect(frame)

                # Compute rolling FPS
                elapsed = time.time() - fps_time
                current_fps = fps_frames / elapsed if elapsed > 0 else 0.0

                # Draw overlays and encode only when MJPEG server is active
                if self.mjpeg_server:
                    display_frame = self.draw_detections(frame.copy(), detections)
                    _, jpeg = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    self.mjpeg_server.update_frame(jpeg.tobytes())

                # Publish to MQTT
                self.publish_detections(detections, fps=current_fps)

                # Log FPS every 10 seconds
                if elapsed >= 10:
                    logger.info(f"FPS: {current_fps:.1f} | Detections: {len(detections)} | Frames: {self.frame_count}")
                    fps_time = time.time()
                    fps_frames = 0

            except Exception as e:
                logger.error(f"Detection loop error: {e}", exc_info=True)
                time.sleep(0.1)

        # Cleanup
        logger.info("Shutting down...")
        cap.release()
        if self.mjpeg_server:
            self.mjpeg_server.stop()
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        logger.info("Jetson vision detector stopped")


if __name__ == "__main__":
    detector = JetsonVisionDetector()
    detector.run()
