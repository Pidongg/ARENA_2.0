# %%
import os
import sys
import re
import time
import torch as t
import numpy as np
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Iterable, Optional, Union, Dict, List, Tuple
from torch.utils.data import DataLoader
from tqdm import tqdm

Arr = np.ndarray
grad_tracking_enabled = True

# Make sure exercises are in the path
chapter = r"chapter0_fundamentals"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = exercises_dir / "part5_backprop"
if str(exercises_dir) not in sys.path: sys.path.append(str(exercises_dir))
os.chdir(section_dir)

import part5_backprop.tests as tests
from part5_backprop.utils import visualize, get_mnist
from plotly_utils import line

MAIN = __name__ == "__main__"
# %%
def log_back(grad_out: Arr, out: Arr, x: Arr) -> Arr:
    '''Backwards function for f(x) = log(x)

    grad_out: Gradient of some loss wrt out
    out: the output of np.log(x).
    x: the input of np.log.

    Return: gradient of the given loss wrt x
    '''
    return grad_out * (1 / x)


if MAIN:
    tests.test_log_back(log_back)
# %%
def unbroadcast(broadcasted: Arr, original: Arr) -> Arr:
    '''
    Sum 'broadcasted' until it has the shape of 'original'.

    broadcasted: An array that was formerly of the same shape of 'original' and was expanded by broadcasting rules.
    '''
    n_dims_to_sum = len(broadcasted.shape) - len(original.shape)
    broadcasted = broadcasted.sum(axis=tuple(range(n_dims_to_sum)))

    dims_to_sum = tuple([
        i for i, (o, b) in enumerate(zip(original.shape, broadcasted.shape))
        if o == 1 and b > 1
    ])
    broadcasted = broadcasted.sum(axis=dims_to_sum, keepdims=True)

    return broadcasted


if MAIN:
    tests.test_unbroadcast(unbroadcast)
# %%
def multiply_back0(grad_out: Arr, out: Arr, x: Arr, y: Union[Arr, float]) -> Arr:
    '''Backwards function for x * y wrt argument 0 aka x.'''
    if not isinstance(y, Arr):
        y = np.array(y)
    return unbroadcast(y * grad_out, x)

def multiply_back1(grad_out: Arr, out: Arr, x: Union[Arr, float], y: Arr) -> Arr:
    '''Backwards function for x * y wrt argument 1 aka y.'''
    if not isinstance(x, Arr):
        x = np.array(x)
    return unbroadcast(x * grad_out, y)


if MAIN:
    tests.test_multiply_back(multiply_back0, multiply_back1)
    tests.test_multiply_back_float(multiply_back0, multiply_back1)
# %%
def forward_and_back(a: Arr, b: Arr, c: Arr) -> Tuple[Arr, Arr, Arr]:
    '''
    Calculates the output of the computational graph above (g), then backpropogates the gradients and returns dg/da, dg/db, and dg/dc
    '''
    d = a * b
    e = np.log(c)
    f = d * e
    g = np.log(f)

    final_grad_out = np.array([1.0])

    dg_df  = log_back(final_grad_out, g, f)
    dg_dd = multiply_back0(dg_df, f, d, e)
    dg_de = multiply_back1(dg_df, f, d, e)
    dg_da = multiply_back0(dg_dd, d, a, b)
    dg_db = multiply_back1(dg_dd, d, a, b)
    dg_dc = log_back(dg_de, e, c)

    return (dg_da, dg_db, dg_dc)


if MAIN:
    tests.test_forward_and_back(forward_and_back)
# %%
@dataclass(frozen=True)
class Recipe:
    '''Extra information necessary to run backpropagation. You don't need to modify this.'''

    func: Callable
    "The 'inner' NumPy function that does the actual forward computation."
    "Note, we call it 'inner' to distinguish it from the wrapper we'll create for it later on."

    args: tuple
    "The input arguments passed to func."
    "For instance, if func was np.sum then args would be a length-1 tuple containing the tensor to be summed."

    kwargs: Dict[str, Any]
    "Keyword arguments passed to func."
    "For instance, if func was np.sum then kwargs might contain 'dim' and 'keepdims'."

    parents: Dict[int, "Tensor"]
    "Map from positional argument index to the Tensor at that position, in order to be able to pass gradients back along the computational graph."
# %%
class BackwardFuncLookup:
    def __init__(self) -> None:
        self.back_funcs: defaultdict[Callable, dict[int, Callable]] = defaultdict(dict)

    def add_back_func(self, forward_fn: Callable, arg_position: int, back_fn: Callable) -> None:
        self.back_funcs[forward_fn][arg_position] = back_fn


    def get_back_func(self, forward_fn: Callable, arg_position: int) -> Callable:
        return self.back_funcs[forward_fn][arg_position]


if MAIN:
    BACK_FUNCS = BackwardFuncLookup()
    BACK_FUNCS.add_back_func(np.log, 0, log_back)
    BACK_FUNCS.add_back_func(np.multiply, 0, multiply_back0)
    BACK_FUNCS.add_back_func(np.multiply, 1, multiply_back1)

    assert BACK_FUNCS.get_back_func(np.log, 0) == log_back
    assert BACK_FUNCS.get_back_func(np.multiply, 0) == multiply_back0
    assert BACK_FUNCS.get_back_func(np.multiply, 1) == multiply_back1

    print("Tests passed - BackwardFuncLookup class is working as expected!")
# %%
Arr = np.ndarray

class Tensor:
    '''
    A drop-in replacement for torch.Tensor supporting a subset of features.
    '''

    array: Arr
    "The underlying array. Can be shared between multiple Tensors."
    requires_grad: bool
    "If True, calling functions or methods on this tensor will track relevant data for backprop."
    grad: Optional["Tensor"]
    "Backpropagation will accumulate gradients into this field."
    recipe: Optional[Recipe]
    "Extra information necessary to run backpropagation."

    def __init__(self, array: Union[Arr, list], requires_grad=False):
        self.array = array if isinstance(array, Arr) else np.array(array)
        self.requires_grad = requires_grad
        self.grad = None
        self.recipe = None
        "If not None, this tensor's array was created via recipe.func(*recipe.args, **recipe.kwargs)."

    def __neg__(self) -> "Tensor":
        return negative(self)

    def __add__(self, other) -> "Tensor":
        return add(self, other)

    def __radd__(self, other) -> "Tensor":
        return add(other, self)

    def __sub__(self, other) -> "Tensor":
        return subtract(self, other)

    def __rsub__(self, other):
        return subtract(other, self)

    def __mul__(self, other) -> "Tensor":
        return multiply(self, other)

    def __rmul__(self, other) -> "Tensor":
        return multiply(other, self)

    def __truediv__(self, other) -> "Tensor":
        return true_divide(self, other)

    def __rtruediv__(self, other) -> "Tensor":
        return true_divide(other, self)

    def __matmul__(self, other) -> "Tensor":
        return matmul(self, other)

    def __rmatmul__(self, other) -> "Tensor":
        return matmul(other, self)

    def __eq__(self, other) -> "Tensor":
        return eq(self, other)

    def __repr__(self) -> str:
        return f"Tensor({repr(self.array)}, requires_grad={self.requires_grad})"

    def __len__(self) -> int:
        if self.array.ndim == 0:
            raise TypeError
        return self.array.shape[0]

    def __hash__(self) -> int:
        return id(self)

    def __getitem__(self, index) -> "Tensor":
        return getitem(self, index)

    def add_(self, other: "Tensor", alpha: float = 1.0) -> "Tensor":
        add_(self, other, alpha=alpha)
        return self

    @property
    def T(self) -> "Tensor":
        return permute(self)

    def item(self):
        return self.array.item()

    def sum(self, dim=None, keepdim=False):
        return sum(self, dim=dim, keepdim=keepdim)

    def log(self):
        return log(self)

    def exp(self):
        return exp(self)

    def reshape(self, new_shape):
        return reshape(self, new_shape)

    def expand(self, new_shape):
        return expand(self, new_shape)

    def permute(self, dims):
        return permute(self, dims)

    def maximum(self, other):
        return maximum(self, other)

    def relu(self):
        return relu(self)

    def argmax(self, dim=None, keepdim=False):
        return argmax(self, dim=dim, keepdim=keepdim)

    def uniform_(self, low: float, high: float) -> "Tensor":
        self.array[:] = np.random.uniform(low, high, self.array.shape)
        return self

    def backward(self, end_grad: Union[Arr, "Tensor", None] = None) -> None:
        if isinstance(end_grad, Arr):
            end_grad = Tensor(end_grad)
        return backprop(self, end_grad)

    def size(self, dim: Optional[int] = None):
        if dim is None:
            return self.shape
        return self.shape[dim]

    @property
    def shape(self):
        return self.array.shape

    @property
    def ndim(self):
        return self.array.ndim

    @property
    def is_leaf(self):
        '''Same as https://pytorch.org/docs/stable/generated/torch.Tensor.is_leaf.html'''
        if self.requires_grad and self.recipe and self.recipe.parents:
            return False
        return True

    def __bool__(self):
        if np.array(self.shape).prod() != 1:
            raise RuntimeError("bool value of Tensor with more than one value is ambiguous")
        return bool(self.item())

def empty(*shape: int) -> Tensor:
    '''Like torch.empty.'''
    return Tensor(np.empty(shape))

def zeros(*shape: int) -> Tensor:
    '''Like torch.zeros.'''
    return Tensor(np.zeros(shape))

def arange(start: int, end: int, step=1) -> Tensor:
    '''Like torch.arange(start, end).'''
    return Tensor(np.arange(start, end, step=step))

def tensor(array: Arr, requires_grad=False) -> Tensor:
    '''Like torch.tensor.'''
    return Tensor(array, requires_grad=requires_grad)
# %%
def log_forward(x: Tensor) -> Tensor:
    '''Performs np.log on a Tensor object.'''
    array = np.log(x.array)
    
    requires_grad = grad_tracking_enabled and (x.requires_grad or (x.recipe is not None))

    out = Tensor(array, requires_grad)

    if requires_grad:
        out.recipe = Recipe(func=np.log, args=(x.array,), kwargs={}, parents={0: x})
    else:
        out.recipe = None

    return out

if MAIN:
    log = log_forward
    tests.test_log(Tensor, log_forward)
    tests.test_log_no_grad(Tensor, log_forward)
    a = Tensor([1], requires_grad=True)
    grad_tracking_enabled = False
    b = log_forward(a)
    grad_tracking_enabled = True
    assert not b.requires_grad, "should not require grad if grad tracking globally disabled"
    assert b.recipe is None, "should not create recipe if grad tracking globally disabled"
# %%
def multiply_forward(a: Union[Tensor, int], b: Union[Tensor, int]) -> Tensor:
    '''Performs np.log on a Tensor object.'''
    assert isinstance(a, Tensor) or isinstance(b, Tensor)

    arg_a = a.array if isinstance(a, Tensor) else a
    arg_b = b.array if isinstance(b, Tensor) else b

    requires_grad = grad_tracking_enabled and any([
        (isinstance(x, Tensor) and (x.requires_grad or x.recipe is not None)) for x in (a, b)
    ])

    out_arr = arg_a * arg_b
    assert isinstance(out_arr, np.ndarray)

    out = Tensor(out_arr, requires_grad)

    if requires_grad:
        parents = {idx: arr for idx, arr in enumerate([a, b]) if isinstance(arr, Tensor)}
        out.recipe = Recipe(np.multiply, (arg_a, arg_b), {}, parents)

    return out



if MAIN:
    multiply = multiply_forward
    tests.test_multiply(Tensor, multiply_forward)
    tests.test_multiply_no_grad(Tensor, multiply_forward)
    tests.test_multiply_float(Tensor, multiply_forward)
    a = Tensor([2], requires_grad=True)
    b = Tensor([3], requires_grad=True)
    grad_tracking_enabled = False
    b = multiply_forward(a, b)
    grad_tracking_enabled = True
    assert not b.requires_grad, "should not require grad if grad tracking globally disabled"
    assert b.recipe is None, "should not create recipe if grad tracking globally disabled"
# %%
def wrap_forward_fn(numpy_func: Callable, is_differentiable=True) -> Callable:
    '''
    numpy_func: Callable
        takes any number of positional arguments, some of which may be NumPy arrays, and 
        any number of keyword arguments which we aren't allowing to be NumPy arrays at 
        present. It returns a single NumPy array.

    is_differentiable: 
        if True, numpy_func is differentiable with respect to some input argument, so we 
        may need to track information in a Recipe. If False, we definitely don't need to
        track information.

    Return: Callable
        It has the same signature as numpy_func, except wherever there was a NumPy array, 
        this has a Tensor instead.
    '''

    def tensor_func(*args: Any, **kwargs: Any) -> Tensor:
        arg_arrays = tuple([(a.array if isinstance(a, Tensor) else a) for a in args])
        out_arr = numpy_func(*arg_arrays, **kwargs)

        requires_grad = grad_tracking_enabled and is_differentiable and any([
            (isinstance(a, Tensor) and (a.requires_grad or a.recipe is not None)) for a in args
        ])

        out = Tensor(out_arr, requires_grad)

        if requires_grad:
            parents = {idx: a for idx, a in enumerate(args) if isinstance(a, Tensor)}
            out.recipe = Recipe(numpy_func, arg_arrays, kwargs, parents)

        return out

    return tensor_func

def _sum(x: Arr, dim=None, keepdim=False) -> Arr:
    # need to be careful with sum, because kwargs have different names in torch and numpy
    return np.sum(x, axis=dim, keepdims=keepdim)


if MAIN:
    log = wrap_forward_fn(np.log)
    multiply = wrap_forward_fn(np.multiply)
    eq = wrap_forward_fn(np.equal, is_differentiable=False)
    sum = wrap_forward_fn(_sum)

    tests.test_log(Tensor, log)
    tests.test_log_no_grad(Tensor, log)
    tests.test_multiply(Tensor, multiply)
    tests.test_multiply_no_grad(Tensor, multiply)
    tests.test_multiply_float(Tensor, multiply)
    tests.test_sum(Tensor)
# %%
class Node:
    def __init__(self, *children):
        self.children = list(children)


def get_children(node: Node) -> List[Node]:
    return node.children


def topological_sort(node: Node, get_children: Callable) -> List[Node]:
    '''
    Return a list of node's descendants in reverse topological order from future to past (i.e. `node` should be last).

    Should raise an error if the graph with `node` as root is not in fact acyclic.
    '''
    # SOLUTION

    result: List[Node] = [] # stores the list of nodes to be returned (in reverse topological order)
    perm: set[Node] = set() # same as `result`, but as a set (faster to check for membership)
    temp: set[Node] = set() # keeps track of previously visited nodes (to detect cyclicity)

    def visit(cur: Node):
        '''
        Recursive function which visits all the children of the current node, and appends them all
        to `result` in the order they were found.
        '''
        if cur in perm:
            return
        if cur in temp:
            raise ValueError("Not a DAG!")
        temp.add(cur)

        for next in get_children(cur):
            visit(next)

        result.append(cur)
        perm.add(cur)
        temp.remove(cur)

    visit(node)
    return result
# %%
def sorted_computational_graph(tensor: Tensor) -> List[Tensor]:
    '''
    For a given tensor, return a list of Tensors that make up the nodes of the given Tensor's computational graph, 
    in reverse topological order (i.e. `tensor` should be first).
    '''
    # SOLUTION

    def get_parents(tensor: Tensor) -> List[Tensor]:
        if tensor.recipe is None:
            return []
        return list(tensor.recipe.parents.values())

    return topological_sort(tensor, get_parents)[::-1]
# %%
def backprop(end_node: Tensor, end_grad: Optional[Tensor] = None) -> None:
    '''Accumulates gradients in the grad field of each leaf node.

    tensor.backward() is equivalent to backprop(tensor).

    end_node: 
        The rightmost node in the computation graph. 
        If it contains more than one element, end_grad must be provided.
    end_grad: 
        A tensor of the same shape as end_node. 
        Set to 1 if not specified and end_node has only one element.
    '''
    # Get value of end_grad_arr
    end_grad_arr = np.ones_like(end_node.array) if end_grad is None else end_grad.array

    # Create dict to store gradients
    grads: Dict[Tensor, Arr] = {end_node: end_grad_arr}

    # Iterate through the computational graph, using your sorting function
    for node in sorted_computational_graph(end_node):

        # Get the outgradient from the grads dict
        outgrad = grads.pop(node)
        # We only store the gradients if this node is a leaf & requires_grad is true
        if node.is_leaf and node.requires_grad:
            # Add the gradient to this node's grad (need to deal with special case grad=None)
            if node.grad is None:
                node.grad = Tensor(outgrad)
            else:
                node.grad.array += outgrad

        # If node has no parents, then the backtracking through the computational
        # graph ends here
        if node.recipe is None or node.recipe.parents is None:
            continue

        # If node has a recipe, then we iterate through parents (which is a dict of {arg_posn: tensor})
        for argnum, parent in node.recipe.parents.items():

            # Get the backward function corresponding to the function that created this node
            back_fn = BACK_FUNCS.get_back_func(node.recipe.func, argnum)

            # Use this backward function to calculate the gradient
            in_grad = back_fn(outgrad, node.array, *node.recipe.args, **node.recipe.kwargs)

            # Add the gradient to this node in the dictionary `grads`
            # Note that we only set node.grad (from the grads dict) in the code block above
            if parent not in grads:
                grads[parent] = in_grad
            else:
                grads[parent] += in_grad


if MAIN:
    tests.test_backprop(Tensor)
    tests.test_backprop_branching(Tensor)
    tests.test_backprop_requires_grad_false(Tensor)
    tests.test_backprop_float_arg(Tensor)
# %%
