from gym.spaces import Dict, Discrete, Tuple, MultiDiscrete, Box

from ray.rllib.models.torch.torch_modelv2 import TorchModelV2
from ray.rllib.models.torch.fcnet import FullyConnectedNetwork as TorchFC
from ray.rllib.utils.torch_ops import FLOAT_MIN
import numpy as np
from typing import Dict, List, Any, Union
from ray.rllib.models.modelv2 import ModelV2
from ray.rllib.models.torch.recurrent_net import RecurrentNetwork as TorchRNN
from ray.rllib.utils.annotations import override
from ray.rllib.utils.framework import try_import_tf, try_import_torch, \
    TensorType
from ray.rllib.policy.rnn_sequencing import add_time_dimension
from ray.rllib.models.torch.misc import SlimFC

tf1, tf, tfv = try_import_tf()
torch, nn = try_import_torch()


class Torch_ActionMask_LSTM_CentralizedCritic_Model(TorchRNN, nn.Module):
    """Multi-agent model that implements a centralized VF."""

    def __init__(
            self,
            obs_space,
            action_space,
            num_outputs,
            model_config,
            name,
            fc_size=64,
            lstm_state_size=256,
            **kwargs,
    ):
        full_obs_space = getattr(obs_space, "original_space", obs_space)
        self.obs_size = full_obs_space['obs'].shape[0]
        self.fc_size = fc_size
        self.lstm_state_size = lstm_state_size
        self.n_agents = model_config["custom_model_config"]["ally_num"]
        self.obs_dim = model_config["custom_model_config"]['self_obs_dim']
        self.state_dim = model_config["custom_model_config"]['state_dim']

        nn.Module.__init__(self)
        super().__init__(obs_space, action_space, num_outputs, model_config,
                         name)

        # Build the Module from fc + LSTM + 2xfc (action + value outs).
        self.fc1 = nn.Linear(self.obs_size, self.fc_size)
        self.lstm = nn.LSTM(
            self.fc_size, self.lstm_state_size, batch_first=True)
        self.action_branch = nn.Linear(self.lstm_state_size, num_outputs)
        self.value_branch = nn.Linear(self.lstm_state_size, 1)

        # Holds the current "base" output (before logits layer).
        self._features = None

        # Central VF maps (obs, opp_obs, opp_act) -> vf_pred
        input_size = self.obs_dim + self.state_dim + num_outputs * (self.n_agents - 1)  # obs + opp_obs + opp_act
        self.central_vf = nn.Sequential(
            SlimFC(input_size, 32, activation_fn=nn.Tanh),
            SlimFC(32, 1),
        )

        self.coma_flag = False
        if "coma" in model_config["custom_model_config"]:
            self.coma_flag = True
            self.value_branch = nn.Linear(self.lstm_state_size, num_outputs)
            self.central_vf = nn.Sequential(
                SlimFC(input_size, 16, activation_fn=nn.Tanh),
                SlimFC(16, num_outputs),
            )

    @override(TorchRNN)
    def get_initial_state(self):
        # TODO: (sven): Get rid of `get_initial_state` once Trajectory
        #  View API is supported across all of RLlib.
        # Place hidden states on same device as model.
        h = [
            self.fc1.weight.new(1, self.lstm_state_size).zero_().squeeze(0),
            self.fc1.weight.new(1, self.lstm_state_size).zero_().squeeze(0)
        ]
        return h

    @override(TorchRNN)
    def forward(self, input_dict: Dict[str, TensorType],
                state: List[TensorType],
                seq_lens: TensorType) -> (TensorType, List[TensorType]):
        """Adds time dimension to batch before sending inputs to forward_rnn().

        You should implement forward_rnn() in your subclass."""
        flat_inputs = input_dict["obs"]["obs"].float()
        if isinstance(seq_lens, np.ndarray):
            seq_lens = torch.Tensor(seq_lens).int()
        max_seq_len = flat_inputs.shape[0] // seq_lens.shape[0]
        self.time_major = self.model_config.get("_time_major", False)
        inputs = add_time_dimension(
            flat_inputs,
            max_seq_len=max_seq_len,
            framework="torch",
            time_major=self.time_major,
        )
        output, new_state = self.forward_rnn(inputs, state, seq_lens)
        output = torch.reshape(output, [-1, self.num_outputs])

        # Convert action_mask into a [0.0 || -inf]-type mask.
        action_mask = input_dict["obs"]["action_mask"]
        inf_mask = torch.clamp(torch.log(action_mask), min=FLOAT_MIN)
        masked_output = output + inf_mask

        return masked_output, new_state

    @override(TorchRNN)
    def forward_rnn(self, inputs, state, seq_lens):

        # Compute the unmasked logits.
        x = nn.functional.relu(self.fc1(inputs))
        self._features, [h, c] = self.lstm(
            x, [torch.unsqueeze(state[0], 0),
                torch.unsqueeze(state[1], 0)])
        logits = self.action_branch(self._features)

        # Return masked logits.
        return logits, [torch.squeeze(h, 0), torch.squeeze(c, 0)]

    # here we use individual observation + global state + other opp input of critic
    def central_value_function(self, obs, state, opponent_actions):
        opponent_actions_one_hot = [
            torch.nn.functional.one_hot(opponent_actions[:, i].long(), self.num_outputs).float()
            for i in
            range(opponent_actions.shape[1])]
        input_ = torch.cat([obs, state] + opponent_actions_one_hot, 1)
        if self.coma_flag:
            return torch.reshape(self.central_vf(input_), [-1, self.num_outputs])
        else:
            return torch.reshape(self.central_vf(input_), [-1])

    @override(TorchRNN)
    def value_function(self) -> TensorType:
        assert self._features is not None, "must call forward() first"
        if self.coma_flag:
            return torch.reshape(self.value_branch(self._features), [-1, self.num_outputs])
        else:
            return torch.reshape(self.value_branch(self._features), [-1])