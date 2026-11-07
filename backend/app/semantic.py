"""Offline multilingual E5 ONNX embeddings. Never resolves/downloads a model."""
import hashlib
import json
from pathlib import Path
import threading
import numpy as np


class LocalE5:
    supports_query_prefix = True
    def __init__(self, directory):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        path = Path(directory)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            filename = (path / item["file"]).resolve(strict=True)
            if not filename.is_relative_to(path.resolve()):
                raise ValueError("Invalid local model manifest path.")
            if hashlib.sha256(filename.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Local embedding model checksum mismatch.")
        self.identity = "onnx-e5-mean-v1:"+hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()[:32]
        self.tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path / "model_quantized.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.lock = threading.BoundedSemaphore(2)

    def encode(self, texts, normalize_embeddings=True, query=False):
        prefix = "query: " if query else "passage: "
        output = []
        with self.lock:
            for offset in range(0, len(texts), 8):
                encoded = self.tokenizer.encode_batch([prefix+t for t in texts[offset:offset+8]])
                values = dict(input_ids=np.asarray([e.ids for e in encoded], dtype=np.int64),
                    attention_mask=np.asarray([e.attention_mask for e in encoded], dtype=np.int64),
                    token_type_ids=np.asarray([e.type_ids for e in encoded], dtype=np.int64))
                hidden = self.session.run(None, {i.name: values[i.name] for i in self.session.get_inputs()})[0]
                mask = values["attention_mask"][..., None]
                pooled = (hidden*mask).sum(axis=1)/np.maximum(mask.sum(axis=1), 1)
                if pooled.shape[1] != 384 or not np.isfinite(pooled).all():
                    raise ValueError("The semantic model must produce finite 384-dimensional vectors.")
                pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
                output.extend(pooled)
        return np.asarray(output)
