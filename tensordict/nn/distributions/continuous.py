# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from numbers import Number
from typing import Sequence

import numpy as np

import torch

from tensordict.nn.utils import mappings
from torch import distributions as D, nn

# We need this to build the distribution maps
__all__ = [
    "NormalParamExtractor",
    "AddStateIndependentNormalScale",
    "Delta",
]

# speeds up distribution construction
# D.Distribution.set_default_validate_args(False)


class NormalParamWrapper(nn.Module):
    def __init__(
        self,
        operator: nn.Module,
        scale_mapping: str = "biased_softplus_1.0",
        scale_lb: Number = 1e-4,
    ) -> None:
        raise RuntimeError(
            "NormalParamWrapper has been deprecated in favor of `tensordict.nn.NormalParamExtractor`. Use this class instead."
        )


class NormalParamExtractor(nn.Module):
    """A non-parametric nn.Module that splits its input into loc and scale parameters.

    The scale parameters are mapped onto positive values using the specified ``scale_mapping``.

    Args:
        scale_mapping (str, optional): positive mapping function to be used with the std.
            default = ``"biased_softplus_1.0"`` (i.e. softplus map with bias such that fn(0.0) = 1.0)
            choices: ``"softplus"``, ``"exp"``, ``"relu"``, ``"biased_softplus_1"`` or ``"none"`` (no mapping).
            See :func:`~tensordict.nn.mappings` for more details.
        scale_lb (Number, optional): The minimum value that the variance can take. Default is 1e-4.

    Examples:
        >>> import torch
        >>> from tensordict.nn.distributions import NormalParamExtractor
        >>> from torch import nn
        >>> module = nn.Linear(3, 4)
        >>> normal_params = NormalParamExtractor()
        >>> tensor = torch.randn(3)
        >>> loc, scale = normal_params(module(tensor))
        >>> print(loc.shape, scale.shape)
        torch.Size([2]) torch.Size([2])
        >>> assert (scale > 0).all()
        >>> # with modules that return more than one tensor
        >>> module = nn.LSTM(3, 4)
        >>> tensor = torch.randn(4, 2, 3)
        >>> loc, scale, others = normal_params(*module(tensor))
        >>> print(loc.shape, scale.shape)
        torch.Size([4, 2, 2]) torch.Size([4, 2, 2])
        >>> assert (scale > 0).all()

    """

    def __init__(
        self,
        scale_mapping: str = "biased_softplus_1.0",
        scale_lb: Number = 1e-4,
    ) -> None:
        super().__init__()
        self.scale_mapping = mappings(scale_mapping)
        self.scale_lb = scale_lb

    def forward(self, *tensors: torch.Tensor) -> tuple[torch.Tensor, ...]:
        tensor, *others = tensors
        loc, scale = tensor.chunk(2, -1)
        scale = self.scale_mapping(scale).clamp_min(self.scale_lb)
        return (loc, scale, *others)


class AddStateIndependentNormalScale(torch.nn.Module):
    """A nn.Module that adds trainable state-independent scale parameters.

    The scale parameters are mapped onto positive values using the specified ``scale_mapping``.

    Args:
        scale_shape (torch.Size or equivalent, optional): the shape of the scale parameter.
            Defaults to ``torch.Size(())``.

    Keyword Args:
        scale_mapping (str, optional): positive mapping function to be used with the std.
            Defaults to ``"exp"``,
            choices: ``"softplus"``, ``"exp"``, ``"relu"``, ``"biased_softplus_1"``.
        scale_lb (Number, optional): The minimum value that the variance can take.
            Defaults to ``1e-4``.
        device (torch.device, optional): the device of the module.
        make_param (bool, optional): whether the scale should be a parameter (``True``)
            or a buffer (``False``).
            Defaults to ``True``.
        init_value (float, optional): Initial value of state independent scale.
            Defaults to 0.0.

    Examples:
        >>> from torch import nn
        >>> import torch
        >>> num_outputs = 4
        >>> module = nn.Linear(3, num_outputs)
        >>> module_normal = AddStateIndependentNormalScale(num_outputs)
        >>> tensor = torch.randn(3)
        >>> loc, scale = module_normal(module(tensor))
        >>> print(loc.shape, scale.shape)
        torch.Size([4]) torch.Size([4])
        >>> assert (scale > 0).all()
        >>> # with modules that return more than one tensor
        >>> module = nn.LSTM(3, num_outputs)
        >>> module_normal = AddStateIndependentNormalScale(num_outputs)
        >>> tensor = torch.randn(4, 2, 3)
        >>> loc, scale, others = module_normal(*module(tensor))
        >>> print(loc.shape, scale.shape)
        torch.Size([4, 2, 4]) torch.Size([4, 2, 4])
        >>> assert (scale > 0).all()
    """

    def __init__(
        self,
        scale_shape: torch.Size | int | tuple = None,
        *,
        scale_mapping: str = "exp",
        scale_lb: Number = 1e-4,
        device: torch.device | None = None,
        make_param: bool = True,
        init_value: float = 0.0,
    ) -> None:

        super().__init__()
        if scale_shape is None:
            scale_shape = torch.Size(())
        self.scale_lb = scale_lb
        if isinstance(scale_shape, int):
            scale_shape = (scale_shape,)
        self.scale_shape = torch.Size(scale_shape)
        self.scale_mapping = scale_mapping
        if make_param:
            self.state_independent_scale = torch.nn.Parameter(
                torch.full(
                    scale_shape,
                    init_value,
                    dtype=torch.get_default_dtype(),
                    device=device,
                )
            )
        else:
            self.state_independent_scale = torch.nn.Buffer(
                torch.full(
                    scale_shape,
                    init_value,
                    dtype=torch.get_default_dtype(),
                    device=device,
                )
            )

    def forward(
        self, loc: torch.Tensor, *others: torch.Tensor
    ) -> tuple[torch.Tensor, ...]:
        """Forward of AddStateIndependentNormalScale.

        Args:
            loc (torch.Tensor): a location parameter.
            *others: other unused parameters.

        Returns:
            a tuple of two or more tensors containing the ``(loc, scale, *others)`` values.
        """
        if self.scale_shape != loc.shape[-len(self.scale_shape) :]:
            raise RuntimeError(
                f"Last dimensions of loc ({loc.shape[-len(self.scale_shape):]}) do not match the number of dimensions "
                f"in scale ({self.state_independent_scale.shape})"
            )

        scale = self.state_independent_scale.expand_as(loc)
        scale = mappings(self.scale_mapping)(scale).clamp_min(self.scale_lb)

        return (loc, scale, *others)


class Delta(D.Distribution):
    """Delta distribution.

    Args:
        param (torch.Tensor): parameter of the delta distribution;
        atol (number, optional): absolute tolerance to consider that a tensor matches the distribution parameter;
            Default is 1e-6
        rtol (number, optional): relative tolerance to consider that a tensor matches the distribution parameter;
            Default is 1e-6
        batch_shape (torch.Size, optional): batch shape;
        event_shape (torch.Size, optional): shape of the outcome.

    """

    arg_constraints: dict = {}

    def __init__(
        self,
        param: torch.Tensor,
        atol: float = 1e-6,
        rtol: float = 1e-6,
        batch_shape: torch.Size | Sequence[int] | None = None,
        event_shape: torch.Size | Sequence[int] | None = None,
    ) -> None:
        if batch_shape is None:
            batch_shape = torch.Size([])
        if event_shape is None:
            event_shape = torch.Size([])
        self.update(param)
        self.atol = atol
        self.rtol = rtol
        if not len(batch_shape) and not len(event_shape):
            batch_shape = param.shape[:-1]
            event_shape = param.shape[-1:]
        super().__init__(batch_shape=batch_shape, event_shape=event_shape)

    def update(self, param: torch.Tensor) -> None:
        self.param = param

    def _is_equal(self, value: torch.Tensor) -> torch.Tensor:
        param = self.param.expand_as(value)
        is_equal = abs(value - param) < self.atol + self.rtol * abs(param)
        for i in range(-1, -len(self.event_shape) - 1, -1):
            is_equal = is_equal.all(i)
        return is_equal

    def log_prob(self, value: torch.Tensor) -> torch.Tensor:
        is_equal = self._is_equal(value)
        out = torch.zeros_like(is_equal, dtype=value.dtype)
        out.masked_fill_(is_equal, np.inf)
        out.masked_fill_(~is_equal, -np.inf)
        return out

    @torch.no_grad()
    def sample(
        self,
        sample_shape: torch.Size | Sequence[int] | None = None,
    ) -> torch.Tensor:
        if sample_shape is None:
            sample_shape = torch.Size([])
        return self.param.expand((*sample_shape, *self.param.shape))

    def rsample(
        self,
        sample_shape: torch.Size | Sequence[int] | None = None,
    ) -> torch.Tensor:
        if sample_shape is None:
            sample_shape = torch.Size([])
        return self.param.expand((*sample_shape, *self.param.shape))

    @property
    def mode(self) -> torch.Tensor:
        return self.param

    @property
    def mean(self) -> torch.Tensor:
        return self.param

    @property
    def deterministic_sample(self) -> torch.Tensor:
        return self.param


@property
def _logistic_deterministic_sample(self):
    s = self.loc
    for t in self.transforms:
        s = t(s)
    return s


D.LogisticNormal.deterministic_sample = _logistic_deterministic_sample
