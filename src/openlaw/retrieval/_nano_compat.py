"""Compatibility shim for voyage-4-nano's remote modelling code on transformers 5.x.

voyage-4-nano ships a custom `modeling_qwen3_bidirectional.py` on the Hub, written
against transformers==4.51. Measured against the installed transformers 5.17, it
breaks in two places:

  1. `Qwen3BidirectionalModel` declares no `config_class`; transformers 5.x's
     `trust_remote_code` class resolution needs one.
  2. `forward()` calls `create_causal_mask(input_embeds=..., cache_position=...)`;
     transformers 5.x renamed the first argument to `inputs_embeds` and dropped
     the second from that function's signature entirely.

Rather than trust that HF's copy of the file gets fixed upstream (as of this
writing it has not -- `tests/retrieval/test_nano_compat.py` pins the exact broken
snippets), we vendor a corrected copy below and always install it into a local
working copy of the model directory before load, regardless of where that
directory's other files came from. If a future transformers release breaks the
vendored copy too, loading raises `NanoCompatError` loudly (see
`openlaw.retrieval.local_nano`) instead of silently returning bad embeddings.
"""

import shutil
from pathlib import Path

MODELING_FILENAME = "modeling_qwen3_bidirectional.py"

_VENDORED_MODELING_SOURCE = '''"""Bidirectional Qwen3 model for voyage-4-nano embeddings.

Vendored and corrected copy of the modelling code voyage-4-nano ships on the Hub.
See openlaw.retrieval._nano_compat for why this file exists and replaces it.
"""

from typing import Callable

import torch
import torch.nn as nn
from transformers import PreTrainedModel, Qwen3Config, Qwen3Model
from transformers.cache_utils import Cache
from transformers.masking_utils import create_causal_mask
from transformers.modeling_outputs import BaseModelOutputWithPooling
from transformers.processing_utils import Unpack
from transformers.utils import TransformersKwargs


# From modeling_t5gemma.py
def bidirectional_mask_function(attention_mask: torch.Tensor | None) -> Callable:
    """
    This creates bidirectional attention mask.
    """

    def inner_mask(batch_idx: int, head_idx: int, q_idx: int, kv_idx: int) -> bool:
        if attention_mask is None:
            return torch.ones((), dtype=torch.bool)
        return attention_mask[batch_idx, kv_idx].to(torch.bool)

    return inner_mask


class Qwen3BidirectionalModel(PreTrainedModel):
    config_class = Qwen3Config
    _supports_flash_attn = True
    _supports_sdpa = True

    def __init__(self, config):
        super().__init__(config)
        self.model = Qwen3Model(config)
        self.linear = nn.Linear(config.hidden_size, config.num_labels, bias=False)
        self.post_init()

    def post_init(self):
        super().post_init()
        # Override to set all layers to non-causal attention. This'll work with attn_implementation="flash_attention_2" or "sdpa"
        for layer in self.model.layers:
            layer.self_attn.is_causal = False

    def forward(
        self,
        input_ids: torch.LongTensor | None = None,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.LongTensor | None = None,
        past_key_values: Cache | None = None,
        inputs_embeds: torch.FloatTensor | None = None,
        use_cache: bool | None = None,
        cache_position: torch.LongTensor | None = None,
        **kwargs: Unpack[TransformersKwargs],
    ) -> BaseModelOutputWithPooling:
        if inputs_embeds is None:
            inputs_embeds = self.model.embed_tokens(input_ids)
            input_ids = None

        attention_mask = {
            "full_attention": create_causal_mask(
                config=self.config,
                inputs_embeds=inputs_embeds,
                attention_mask=attention_mask,
                past_key_values=None,
                position_ids=position_ids,
                or_mask_function=bidirectional_mask_function(attention_mask),
            )
        }

        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            use_cache=use_cache,
            cache_position=cache_position,
            **kwargs,
        )
        outputs.last_hidden_state = self.linear(outputs.last_hidden_state)
        return outputs
'''


def prepare_local_model_dir(source_dir: Path, working_dir: Path) -> Path:
    """Return a working copy of `source_dir` with our patched modelling file installed.

    Rebuilds `working_dir` from scratch on every call (symlinking the large weight
    files rather than copying their bytes) so callers never depend on whatever
    modelling code `source_dir` happens to contain, and a stale previous working
    copy never lingers.

    Each entry is linked via its fully-resolved absolute path rather than copied
    with `shutil.copytree(symlinks=True)`: the Hugging Face Hub cache stores every
    file as a *relative* symlink into a shared `blobs/` directory, and copying
    that relative link verbatim into a working dir at a different path depth
    produces a broken symlink. Resolving first avoids that, and also avoids ever
    writing through a symlink into the shared, deduplicated HF blob store.
    """
    if working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True)
    for item in source_dir.rglob("*"):
        destination = working_dir / item.relative_to(source_dir)
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(item.resolve())

    modeling_path = working_dir / MODELING_FILENAME
    modeling_path.unlink(missing_ok=True)
    modeling_path.write_text(_VENDORED_MODELING_SOURCE)
    return working_dir


class NanoCompatError(RuntimeError):
    """Raised when voyage-4-nano's remote code cannot be loaded on this transformers.

    Signals that the two known compatibility issues (see module docstring) are no
    longer sufficient -- either transformers moved again, or something else about
    the installed ML stack is incompatible -- so the caller should fail loudly
    rather than fall back to silently degraded (or missing) embeddings.
    """
