# %%

import os; os.environ["ACCELERATE_DISABLE_RICH"] = "1"
import sys
from pathlib import Path
import torch as t
from torch import Tensor
import numpy as np
import pandas as pd
import einops
from tqdm.notebook import tqdm
import plotly.express as px
import webbrowser
import re
import itertools
from jaxtyping import Float, Int, Bool
from typing import List, Optional, Callable, Tuple, Dict, Literal, Set
from functools import partial
from IPython.display import display, HTML
from rich.table import Table, Column
from rich import print as rprint
import circuitsvis as cv
from pathlib import Path
from transformer_lens.hook_points import HookPoint
from transformer_lens import utils, HookedTransformer, ActivationCache
from transformer_lens.components import Embed, Unembed, LayerNorm, MLP

t.set_grad_enabled(False)

# Make sure exercises are in the path
chapter = r"chapter1_transformers"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = (exercises_dir / "part3_indirect_object_identification").resolve()
if str(exercises_dir) not in sys.path: sys.path.append(str(exercises_dir))

from plotly_utils import imshow, line, scatter, bar
import part3_indirect_object_identification.tests as tests

device = t.device("cuda") if t.cuda.is_available() else t.device("cpu")

MAIN = __name__ == "__main__"

# %%

model = HookedTransformer.from_pretrained(
    "gpt2-small",
    center_unembed=True,
    center_writing_weights=True,
    fold_ln=True,
    refactor_factored_attn_matrices=True,
)
# %%
# Show column norms are the same (except first few, for fiddly bias reasons)
line([model.W_Q[0, 0].pow(2).sum(0), model.W_K[0, 0].pow(2).sum(0)])
# Show columns are orthogonal (except first few, again)
W_Q_dot_products = einops.einsum(
    model.W_Q[0, 0], model.W_Q[0, 0], "d_model d_head_1, d_model d_head_2 -> d_head_1 d_head_2"
)
imshow(W_Q_dot_products)
# %%
# Here is where we test on a single prompt
# Result: 70% probability on Mary, as we expect

example_prompt = "After John and Mary went to the store, John gave a bottle of milk to"
example_answer = " Mary"
tokenized_prompt = model.to_tokens(example_prompt)
utils.test_prompt(example_prompt, example_answer, model, prepend_bos=True)

multiplicity = []
mary_probs = []
john_probs = []
for i in range(1, 100, 5):
    multiplicity.append(i)
    def hook_fn(emb: Float[Tensor, "seq_len d_model"], hook: HookPoint):
        emb[:, 4, :] *= i

    if MAIN:
        logits = model.run_with_hooks(
            tokenized_prompt,
            return_type = 'logits',
            fwd_hooks=[
                ('hook_embed', hook_fn)
            ]
        )

        mary_probs.append(logits.softmax(dim=-1)[0, -1, 5335].item())
        john_probs.append(logits.softmax(dim=-1)[0, -1, 1757].item())

df = pd.DataFrame({'mults': multiplicity, 'mary': mary_probs, 'john': john_probs})
df.plot(x='mults')
# %%
prompt_format = [
    "When John and Mary went to the shops,{} gave the bag to",
    "When Tom and James went to the park,{} gave the ball to",
    "When Dan and Sid went to the shops,{} gave an apple to",
    "After Martin and Amy went to the park,{} gave a drink to",
]
name_pairs = [
    (" John", " Mary"),
    (" Tom", " James"),
    (" Dan", " Sid"),
    (" Martin", " Amy"),
]

# Define 8 prompts, in 4 groups of 2 (with adjacent prompts having answers swapped)
prompts = [
    prompt.format(name) 
    for (prompt, names) in zip(prompt_format, name_pairs) for name in names[::-1] 
]
# Define the answers for each prompt, in the form (correct, incorrect)
answers = [names[::i] for names in name_pairs for i in (1, -1)]
# Define the answer tokens (same shape as the answers)
answer_tokens = t.concat([
    model.to_tokens(names, prepend_bos=False).T for names in answers
])

rprint(prompts)
rprint(answers)
rprint(answer_tokens)

table = Table("Prompt", "Correct", "Incorrect", title="Prompts & Answers:")

for prompt, answer in zip(prompts, answers):
    table.add_row(prompt, repr(answer[0]), repr(answer[1]))

rprint(table)
# %%
tokens = model.to_tokens(prompts, prepend_bos=True)
# Move the tokens to the GPU
tokens = tokens.to(device)
# Run the model and cache all activations
original_logits, cache = model.run_with_cache(tokens)
# %%
def logits_to_ave_logit_diff(
    logits: Float[Tensor, "batch seq d_vocab"],
    answer_tokens: Float[Tensor, "batch 2"] = answer_tokens,
    per_prompt: bool = False
):
    '''
    Returns logit difference between the correct and incorrect answer.

    If per_prompt=True, return the array of differences rather than the average.
    '''
    correct_logits = t.gather(logits[:, -1], 1, answer_tokens)
    logit_diff = correct_logits[:, 0] - correct_logits[:, 1]
    if not per_prompt:
        logit_diff = logit_diff.mean(dim=0)
    return logit_diff


tests.test_logits_to_ave_logit_diff(logits_to_ave_logit_diff)

original_per_prompt_diff = logits_to_ave_logit_diff(original_logits, answer_tokens, per_prompt=True)
print("Per prompt logit difference:", original_per_prompt_diff)
original_average_logit_diff = logits_to_ave_logit_diff(original_logits, answer_tokens)
print("Average logit difference:", original_average_logit_diff)

cols = [
    "Prompt", 
    Column("Correct", style="rgb(0,200,0) bold"), 
    Column("Incorrect", style="rgb(255,0,0) bold"), 
    Column("Logit Difference", style="bold")
]
table = Table(*cols, title="Logit differences")

for prompt, answer, logit_diff in zip(prompts, answers, original_per_prompt_diff):
    table.add_row(prompt, repr(answer[0]), repr(answer[1]), f"{logit_diff.item():.3f}")

rprint(table)
# %%
answer_residual_directions: Float[Tensor, "batch 2 d_model"] = model.tokens_to_residual_directions(answer_tokens)
print("Answer residual directions shape:", answer_residual_directions.shape)

correct_residual_directions, incorrect_residual_directions = answer_residual_directions.unbind(dim=1)
logit_diff_directions: Float[Tensor, "batch d_model"] = correct_residual_directions - incorrect_residual_directions
print(f"Logit difference directions shape:", logit_diff_directions.shape)

#%%
# cache syntax - resid_post is the residual stream at the end of the layer, -1 gets the final layer. The general syntax is [activation_name, layer_index, sub_layer_type]. 

final_residual_stream: Float[Tensor, "batch seq d_model"] = cache["resid_post", -1]
print(f"Final residual stream shape: {final_residual_stream.shape}")
final_token_residual_stream: Float[Tensor, "batch d_model"] = final_residual_stream[:, -1, :]

# Apply LayerNorm scaling (to just the final sequence position)
# pos_slice is the subset of the positions we take - here the final token of each prompt
scaled_final_token_residual_stream = cache.apply_ln_to_stack(final_token_residual_stream, layer=-1, pos_slice=-1)

average_logit_diff = einops.einsum(
    scaled_final_token_residual_stream, logit_diff_directions,
    "batch d_model, batch d_model ->"
) / len(prompts)

print(f"Calculated average logit diff: {average_logit_diff:.10f}")
print(f"Original logit difference:     {original_average_logit_diff:.10f}")

t.testing.assert_close(average_logit_diff, original_average_logit_diff)

# %%
def residual_stack_to_logit_diff(
    residual_stack: Float[Tensor, "... batch d_model"], 
    cache: ActivationCache,
    logit_diff_directions: Float[Tensor, "batch d_model"] = logit_diff_directions,
) -> Float[Tensor, "..."]:
    '''
    Gets the avg logit difference between the correct and incorrect answer for a given 
    stack of components in the residual stream.
    '''

    scaled_residual_stream = cache.apply_ln_to_stack(residual_stack, layer=-1, pos_slice=-1)
    average_logit_diff = einops.einsum(
        scaled_residual_stream, logit_diff_directions,
        "... batch d_model, batch d_model -> ..."
    ) / residual_stack.size(-2)

    return average_logit_diff


t.testing.assert_close(
    residual_stack_to_logit_diff(final_token_residual_stream, cache),
    original_average_logit_diff
)
# %%
accumulated_residual, labels = cache.accumulated_resid(layer=-1, incl_mid=True, pos_slice=-1, return_labels=True)
# accumulated_residual has shape (component, batch, d_model)

logit_lens_logit_diffs: Float[Tensor, "component"] = residual_stack_to_logit_diff(accumulated_residual, cache)

line(
    logit_lens_logit_diffs, 
    hovermode="x unified",
    title="Logit Difference From Accumulated Residual Stream",
    labels={"x": "Layer", "y": "Logit Diff"},
    xaxis_tickvals=labels,
    width=800
)
# %%
per_layer_residual, labels = cache.decompose_resid(layer=-1, pos_slice=-1, return_labels=True)
per_layer_logit_diffs = residual_stack_to_logit_diff(per_layer_residual, cache)

line(
    per_layer_logit_diffs, 
    hovermode="x unified",
    title="Logit Difference From Each Layer",
    labels={"x": "Layer", "y": "Logit Diff"},
    xaxis_tickvals=labels,
    width=800
)
# %%
per_head_residual, labels = cache.stack_head_results(layer=-1, pos_slice=-1, return_labels=True)
per_head_residual = einops.rearrange(
    per_head_residual, 
    "(layer head) ... -> layer head ...", 
    layer=model.cfg.n_layers
)
per_head_logit_diffs = residual_stack_to_logit_diff(per_head_residual, cache)

imshow(
    per_head_logit_diffs, 
    labels={"x":"Head", "y":"Layer"}, 
    title="Logit Difference From Each Head",
    width=600
)
# %%
def topk_of_Nd_tensor(tensor: Float[Tensor, "rows cols"], k: int):
    '''
    Helper function: does same as tensor.topk(k).indices, but works over 2D tensors.
    Returns a list of indices, i.e. shape [k, tensor.ndim].

    Example: if tensor is 2D array of values for each head in each layer, this will
    return a list of heads.
    '''
    i = t.topk(tensor.flatten(), k).indices
    return np.array(np.unravel_index(utils.to_numpy(i), tensor.shape)).T.tolist()



k = 3

for head_type in ["Positive", "Negative"]:

    # Get the heads with largest (or smallest) contribution to the logit difference
    top_heads = topk_of_Nd_tensor(per_head_logit_diffs * (1 if head_type=="Positive" else -1), k)

    # Get all their attention patterns
    attn_patterns_for_important_heads: Float[Tensor, "head q k"] = t.stack([
        cache["pattern", layer][:, head].mean(0)
        for layer, head in top_heads
    ])

    # Display results
    display(HTML(f"<h2>Top {k} {head_type} Logit Attribution Heads</h2>"))
    display(cv.attention.attention_patterns(
        attention = attn_patterns_for_important_heads,
        tokens = model.to_str_tokens(tokens[0]),
        attention_head_names = [f"{layer}.{head}" for layer, head in top_heads],
    ))
# %%

from transformer_lens import patching

clean_tokens = tokens
# Swap each adjacent pair to get corrupted tokens
indices = [i+1 if i % 2 == 0 else i-1 for i in range(len(tokens))]
corrupted_tokens = clean_tokens[indices]

print(
    "Clean string 0:    ", model.to_string(clean_tokens[0]), "\n"
    "Corrupted string 0:", model.to_string(corrupted_tokens[0])
)

clean_logits, clean_cache = model.run_with_cache(clean_tokens)
corrupted_logits, corrupted_cache = model.run_with_cache(corrupted_tokens)

clean_logit_diff = logits_to_ave_logit_diff(clean_logits, answer_tokens)
print(f"Clean logit diff: {clean_logit_diff:.4f}")

corrupted_logit_diff = logits_to_ave_logit_diff(corrupted_logits, answer_tokens)
print(f"Corrupted logit diff: {corrupted_logit_diff:.4f}")
# %%
def ioi_metric(
    logits: Float[Tensor, "batch seq d_vocab"], 
    answer_tokens: Float[Tensor, "batch 2"] = answer_tokens,
    corrupted_logit_diff: float = corrupted_logit_diff,
    clean_logit_diff: float = clean_logit_diff,
) -> Float[Tensor, ""]:
    '''
    Linear function of logit diff, calibrated so that it equals 0 when performance is 
    same as on corrupted input, and 1 when performance is same as on clean input.
    '''
    logit_diff = logits_to_ave_logit_diff(logits, answer_tokens)

    return (logit_diff - corrupted_logit_diff) / (clean_logit_diff - corrupted_logit_diff)


t.testing.assert_close(ioi_metric(clean_logits).item(), 1.0)
t.testing.assert_close(ioi_metric(corrupted_logits).item(), 0.0)
t.testing.assert_close(ioi_metric((clean_logits + corrupted_logits) / 2).item(), 0.5)
# %%
act_patch_resid_pre = patching.get_act_patch_resid_pre(
    model = model,
    corrupted_tokens = corrupted_tokens,
    clean_cache = clean_cache,
    patching_metric = ioi_metric
)

labels = [f"{tok} {i}" for i, tok in enumerate(model.to_str_tokens(clean_tokens[0]))]

imshow(
    act_patch_resid_pre, 
    labels={"x": "Position", "y": "Layer"},
    x=labels,
    title="resid_pre Activation Patching",
    width=600
)
# %%
def patch_residual_component(
    corrupted_residual_component: Float[Tensor, "batch pos d_model"],
    hook: HookPoint, 
    pos: int, 
    clean_cache: ActivationCache
) -> Float[Tensor, "batch pos d_model"]:
    '''
    Patches a given sequence position in the residual stream, using the value
    from the clean cache.
    '''
    corrupted_residual_component[:, pos, :] = clean_cache[hook.name][:, pos, :]

def get_act_patch_resid_pre(
    model: HookedTransformer, 
    corrupted_tokens: Float[Tensor, "batch pos"], 
    clean_cache: ActivationCache, 
    patching_metric: Callable[[Float[Tensor, "batch pos d_vocab"]], float]
) -> Float[Tensor, "layer pos"]:
    '''
    Returns an array of results of patching each position at each layer in the residual
    stream, using the value from the clean cache.

    The results are calculated using the patching_metric function, which should be
    called on the model's logit output.
    '''
    patch_outputs = t.zeros(model.cfg.n_layers, corrupted_tokens.shape[-1]).to(device)
    for layer in tqdm(range(model.cfg.n_layers)):
        for pos in range(corrupted_tokens.shape[-1]):
            hook_fn = partial(patch_residual_component, pos=pos, clean_cache=clean_cache)
            logits = model.run_with_hooks(
                corrupted_tokens,
                return_type='logits',
                fwd_hooks=[(
                    utils.get_act_name('resid_pre', layer),
                    hook_fn
                )]
            )
            patch_outputs[layer, pos] = patching_metric(logits)
    return patch_outputs


act_patch_resid_pre_own = get_act_patch_resid_pre(model, corrupted_tokens, clean_cache, ioi_metric)

t.testing.assert_close(act_patch_resid_pre, act_patch_resid_pre_own)
# %%
imshow(
    act_patch_resid_pre_own, 
    x=labels, 
    title="Logit Difference From Patched Residual Stream", 
    labels={"x":"Sequence Position", "y":"Layer"},
    width=600 # If you remove this argument, the plot will usually fill the available space
)
# %%
act_patch_block_every = patching.get_act_patch_block_every(model, corrupted_tokens, clean_cache, ioi_metric)

imshow(
    act_patch_block_every,
    x=labels, 
    facet_col=0, # This argument tells plotly which dimension to split into separate plots
    facet_labels=["Residual Stream", "Attn Output", "MLP Output"], # Subtitles of separate plots
    title="Logit Difference From Patched Attn Head Output", 
    labels={"x": "Sequence Position", "y": "Layer"},
    width=1000,
)
# %%
act_patch_attn_head_out_all_pos = patching.get_act_patch_attn_head_out_all_pos(
    model, 
    corrupted_tokens, 
    clean_cache, 
    ioi_metric
)

imshow(
    act_patch_attn_head_out_all_pos, 
    labels={"y": "Layer", "x": "Head"}, 
    title="attn_head_out Activation Patching (All Pos)",
    width=600
)
# %%
def patch_head_vector(
    corrupted_head_vector: Float[Tensor, "batch pos head_index d_head"],
    hook: HookPoint, 
    head_index: int, 
    clean_cache: ActivationCache
) -> Float[Tensor, "batch pos head_index d_head"]:
    '''
    Patches the output of a given head (before it's added to the residual stream) at
    every sequence position, using the value from the clean cache.
    '''
    corrupted_head_vector[..., head_index, :] = clean_cache[hook.name][..., head_index, :]

def get_act_patch_attn_head_out_all_pos(
    model: HookedTransformer, 
    corrupted_tokens: Float[Tensor, "batch pos"], 
    clean_cache: ActivationCache, 
    patching_metric: Callable
) -> Float[Tensor, "layer head"]:
    '''
    Returns an array of results of patching at all positions for each head in each
    layer, using the value from the clean cache.

    The results are calculated using the patching_metric function, which should be
    called on the model's logit output.
    '''
    result = t.zeros(model.cfg.n_layers, model.cfg.n_heads).to(device)
    for layer in range(model.cfg.n_layers):
        for head_num in range(model.cfg.n_heads):
            hook_fn = partial(patch_head_vector, head_index=head_num, clean_cache=clean_cache)
            logits = model.run_with_hooks(
                corrupted_tokens,
                return_type='logits',
                fwd_hooks=[
                    (utils.get_act_name('z', layer), hook_fn)
                ]
            )
            result[layer][head_num] = patching_metric(logits)
    return result

act_patch_attn_head_out_all_pos_own = get_act_patch_attn_head_out_all_pos(model, corrupted_tokens, clean_cache, ioi_metric)

t.testing.assert_close(act_patch_attn_head_out_all_pos, act_patch_attn_head_out_all_pos_own)

imshow(
    act_patch_attn_head_out_all_pos_own,
    title="Logit Difference From Patched Attn Head Output", 
    labels={"x":"Head", "y":"Layer"},
    width=600
)
# %%
act_patch_attn_head_all_pos_every = patching.get_act_patch_attn_head_all_pos_every(
    model, 
    corrupted_tokens, 
    clean_cache, 
    ioi_metric
)

imshow(
    act_patch_attn_head_all_pos_every, 
    facet_col=0, 
    facet_labels=["Output", "Query", "Key", "Value", "Pattern"],
    title="Activation Patching Per Head (All Pos)", 
    labels={"x": "Head", "y": "Layer"},
)
# %%
# Get all their attention patterns
attn_patterns_for_important_heads: Float[Tensor, "head q k"] = t.stack([
    cache["pattern", layer][:, head].mean(0)
    for layer in range(3, 7) for head in range(10)
])

# Display results
display(cv.attention.attention_patterns(
    attention = attn_patterns_for_important_heads,
    tokens = model.to_str_tokens(tokens[0]),
    attention_head_names = [f"{layer}.{head}" for layer in range(3, 7) for head in range(10)],
))
# %%
from part3_indirect_object_identification.ioi_dataset import NAMES, IOIDataset
# %%
N = 25
ioi_dataset = IOIDataset(
    prompt_type="mixed",
    N=N,
    tokenizer=model.tokenizer,
    prepend_bos=False,
    seed=1,
    device=str(device)
)
# %%
abc_dataset = ioi_dataset.gen_flipped_prompts("ABB->XYZ, BAB->XYZ")
# %%
def format_prompt(sentence: str) -> str:
    '''Format a prompt by underlining names (for rich print)'''
    return re.sub("(" + "|".join(NAMES) + ")", lambda x: f"[u bold dark_orange]{x.group(0)}[/]", sentence) + "\n"


def make_table(cols, colnames, title="", n_rows=5, decimals=4):
    '''Makes and displays a table, from cols rather than rows (using rich print)'''
    table = Table(*colnames, title=title)
    rows = list(zip(*cols))
    f = lambda x: x if isinstance(x, str) else f"{x:.{decimals}f}"
    for row in rows[:n_rows]:
        table.add_row(*list(map(f, row)))
    rprint(table)
# %%
make_table(
    colnames = ["IOI prompt", "IOI subj", "IOI indirect obj", "ABC prompt"],
    cols = [
        map(format_prompt, ioi_dataset.sentences), 
        model.to_string(ioi_dataset.s_tokenIDs).split(), 
        model.to_string(ioi_dataset.io_tokenIDs).split(), 
        map(format_prompt, abc_dataset.sentences), 
    ],
    title = "Sentences from IOI vs ABC distribution",
)
# %%
def logits_to_ave_logit_diff_2(logits: Float[Tensor, "batch seq d_vocab"], ioi_dataset: IOIDataset = ioi_dataset, per_prompt=False):
    '''
    Returns logit difference between the correct and incorrect answer.

    If per_prompt=True, return the array of differences rather than the average.
    '''

    # Only the final logits are relevant for the answer
    # Get the logits corresponding to the indirect object / subject tokens respectively
    io_logits: Float[Tensor, "batch"] = logits[range(logits.size(0)), ioi_dataset.word_idx["end"], ioi_dataset.io_tokenIDs]
    s_logits: Float[Tensor, "batch"] = logits[range(logits.size(0)), ioi_dataset.word_idx["end"], ioi_dataset.s_tokenIDs]
    # Find logit difference
    answer_logit_diff = io_logits - s_logits
    return answer_logit_diff if per_prompt else answer_logit_diff.mean()



model.reset_hooks(including_permanent=True)

ioi_logits_original, ioi_cache = model.run_with_cache(ioi_dataset.toks)
abc_logits_original, abc_cache = model.run_with_cache(abc_dataset.toks)

ioi_per_prompt_diff = logits_to_ave_logit_diff_2(ioi_logits_original, per_prompt=True)
abc_per_prompt_diff = logits_to_ave_logit_diff_2(abc_logits_original, per_prompt=True)

ioi_average_logit_diff = logits_to_ave_logit_diff_2(ioi_logits_original).item()
abc_average_logit_diff = logits_to_ave_logit_diff_2(abc_logits_original).item()

print(f"Average logit diff (IOI dataset): {ioi_average_logit_diff:.4f}")
print(f"Average logit diff (ABC dataset): {abc_average_logit_diff:.4f}")

make_table(
    colnames = ["IOI prompt", "IOI logit diff", "ABC prompt", "ABC logit diff"],
    cols = [
        map(format_prompt, ioi_dataset.sentences), 
        ioi_per_prompt_diff,
        map(format_prompt, abc_dataset.sentences), 
        abc_per_prompt_diff,
    ],
    title = "Sentences from IOI vs ABC distribution",
)
# %%
def ioi_metric_2(
    logits: Float[Tensor, "batch seq d_vocab"],
    clean_logit_diff: float = ioi_average_logit_diff,
    corrupted_logit_diff: float = abc_average_logit_diff,
    ioi_dataset: IOIDataset = ioi_dataset,
) -> float:
    '''
    We calibrate this so that the value is 0 when performance isn't harmed (i.e. same as IOI dataset), 
    and -1 when performance has been destroyed (i.e. is same as ABC dataset).
    '''
    patched_logit_diff = logits_to_ave_logit_diff_2(logits, ioi_dataset)
    return (patched_logit_diff - clean_logit_diff) / (clean_logit_diff - corrupted_logit_diff)


print(f"IOI metric (IOI dataset): {ioi_metric_2(ioi_logits_original):.4f}")
print(f"IOI metric (ABC dataset): {ioi_metric_2(abc_logits_original):.4f}")
# %%
def get_path_patch_head_to_final_resid_post(
    model: HookedTransformer,
    patching_metric: Callable,
    new_dataset: IOIDataset = abc_dataset,
    orig_dataset: IOIDataset = ioi_dataset,
    new_cache: Optional[ActivationCache] = abc_cache,
    orig_cache: Optional[ActivationCache] = ioi_cache,
) -> Float[Tensor, "layer head"]:

    result = t.zeros(model.cfg.n_layers, model.cfg.n_heads)
    # Cache heads on clean/orig input and corrupt/new input
    out, new_cache = model.run_with_cache(
        new_dataset.toks, 
        names_filter=lambda name: name.endswith('z'), 
        return_type=None
    )
    out, orig_cache = model.run_with_cache(
        orig_dataset.toks, 
        names_filter=lambda name: name.endswith('z'), 
        return_type=None
    )
    
    del out
    def patch_sender_with_cache(sender: Float[Tensor, "batch seq_len n_heads d_model"], hook, new_cache, orig_cache, layer, head):
        sender[...] = orig_cache[hook.name][...]
        if layer == hook.layer():
            sender[:, :, head, :] = new_cache[hook.name][:, :, head, :]
        return sender

    for layer in tqdm(range(model.cfg.n_layers)):
        for head in tqdm(range(model.cfg.n_heads)):

            # Patch sender, freeze others, cache the received
            hook_fn = partial(patch_sender_with_cache, new_cache=new_cache, orig_cache=orig_cache, layer=layer, head=head)
            for name in model.hook_dict.keys():
                if name.endswith('hook_z'):
                    model.add_hook(name, hook_fn, level=1)
            out, cache = model.run_with_cache(
                orig_dataset.toks,
                names_filter = lambda name: name.endswith("11.hook_resid_post")
            )
            del out
            
            # Directly compte result
            post_ln = model.ln_final(cache['blocks.11.hook_resid_post'])
            logits = einops.einsum(
                post_ln,
                model.W_U, 
                "batch seq_len d_model, d_model d_vocab -> batch seq_len d_vocab"
            )

            result[layer, head] = patching_metric(logits)
    return result

path_patch_head_to_final_resid_post = get_path_patch_head_to_final_resid_post(model, ioi_metric_2)

imshow(
    100 * path_patch_head_to_final_resid_post,
    title="Direct effect on logit difference",
    labels={"x":"Head", "y":"Layer", "color": "Logit diff. variation"},
    coloraxis=dict(colorbar_ticksuffix = "%"),
    width=600,
)
# %%
def get_path_patch_head_to_heads(
    receiver_heads: List[Tuple[int, int]],
    receiver_input: str,
    model: HookedTransformer,
    patching_metric: Callable,
    new_dataset: IOIDataset = abc_dataset,
    orig_dataset: IOIDataset = ioi_dataset,
    new_cache: Optional[ActivationCache] = None,
    orig_cache: Optional[ActivationCache] = None,
) -> Float[Tensor, "layer head"]:
    '''
    Performs path patching (see algorithm in appendix B of IOI paper), with:

        sender head = (each head, looped through, one at a time)
        receiver node = input to a later head (or set of heads)

    The receiver node is specified by receiver_heads and receiver_input.
    Example (for S-inhibition path patching the queries):
        receiver_heads = [(8, 6), (8, 10), (7, 9), (7, 3)],
        receiver_input = "v"

    Returns:
        tensor of metric values for every possible sender head
    '''
    result = t.zeros(model.cfg.n_layers, model.cfg.n_heads)
    receiver_names = set([utils.get_act_name(receiver_input, layer) for layer, head in receiver_heads])
    
    receiver_filter = lambda name: name in receiver_names
    # Cache heads on clean/orig input and corrupt/new input
    if not new_cache:
        out, new_cache = model.run_with_cache(
            new_dataset.toks, 
            names_filter=lambda name: name.endswith(receiver_input),
            return_type=None
        )
        del out

    if not orig_cache:
        out, orig_cache = model.run_with_cache(
            orig_dataset.toks, 
            names_filter=lambda name: name.endswith(receiver_input), 
            return_type=None
        )
        del out

    def patch_sender_with_cache(sender: Float[Tensor, "batch seq_len n_heads d_model"], hook, new_cache, orig_cache, layer, head):
        # Freeze all layers but the senders
        sender[...] = orig_cache[hook.name][...]
        if layer == hook.layer():
            sender[..., head, :] = new_cache[hook.name][..., head, :]
        return sender

    def patch_receivers(activation: Float[Tensor, "batch seq_len n_heads d_model"], hook, patched_cache, receiver_heads):
        # Activation is a receiver head, patch it with the patched receivers in the patched_cache
        heads_to_patch = t.tensor([head for layer, head in receiver_heads if layer == hook.layer()]).to(device)
        activation[..., heads_to_patch, :] = patched_cache[hook.name][..., heads_to_patch, :]
        return activation
    
    for layer in tqdm(range(model.cfg.n_layers)):
        for head in tqdm(range(model.cfg.n_heads)):
            # Patch sender, freeze others, cache the receiver(s)
            patch_sender_fn = partial(
                patch_sender_with_cache, 
                new_cache=new_cache, 
                orig_cache=orig_cache, 
                layer=layer, 
                head=head
            )
            # Add hooks for every layer
            for name in model.hook_dict.keys():
                if name.endswith(receiver_input):
                    model.add_hook(name, patch_sender_fn, level=1)
            # Cache all the receivers
            out, patched_cache = model.run_with_cache(
                orig_dataset.toks,
                names_filter=receiver_filter
            )
            del out

            # Patch receivers, record output
            patch_receivers_fn = partial(
                patch_receivers,
                patched_cache=patched_cache,
                receiver_heads=receiver_heads
            )
            # Add hooks for only layers that need to be patched
            logits = model.run_with_hooks(
                orig_dataset.toks,
                fwd_hooks=[(lambda name: name in receiver_names, patch_receivers_fn)]
            )
            result[layer, head] = patching_metric(logits)
    return result
# %%
model.reset_hooks()

s_inhibition_value_path_patching_results = get_path_patch_head_to_heads(
    receiver_heads = [(8, 6), (8, 10), (7, 9), (7, 3)],
    receiver_input = "v",
    model = model,
    patching_metric = ioi_metric_2
)

imshow(
    100 * s_inhibition_value_path_patching_results,
    title="Direct effect on S-Inhibition Heads' values", 
    labels={"x": "Head", "y": "Layer", "color": "Logit diff.<br>variation"},
    width=600,
    coloraxis=dict(colorbar_ticksuffix = "%"),
)
# %%
