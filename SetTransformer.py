import torch
from torch import nn


def qkv_attention(queries, keys, values, presence):
    """

    :param queries: Shape (N_j, F)
    :param keys:  Shape (N_i, F)
    :param values:  Shape (N_i, F)
    :param acts:   Shape (N_i, ), should be binary or close to it
    :return:
    """
    n_dims = queries.shape[-1]

    qk = torch.matmul(queries, torch.transpose(keys, 1, 0))  # (N_j, N_i)

    if presence is not None:
        presence = presence.unsqueeze(0)
        qk -= (1.0-presence)*1e32

    qk = torch.softmax(qk/torch.sqrt(n_dims), -1)  # (N_j, N_i)

    return torch.matmul(qk, values)  # (N_j, F)


class MultiHeadQKVAttention(nn.Module):
    def __init__(self, n_feats_in, n_heads):
        super(MultiHeadQKVAttention, self).__init__()

        assert n_feats_in % n_heads == 0

        self.n_heads = n_heads
        self.n_feats = n_feats_in // n_heads

        self.linear_q = nn.Linear(n_feats_in, self.n_feats * n_heads)
        self.linear_k = nn.Linear(n_feats_in, self.n_feats * n_heads)
        self.linear_v = nn.Linear(n_feats_in, self.n_feats * n_heads)

        self.linear_out = nn.Linear(self.n_feats * n_heads, self.n_feats)

    def forward(self, queries, keys, values, presence):
        """

        :param queries: Shape (N_j, F_1)
        :param keys:  Shape (N_i, F_1)
        :param values:  Shape (N_i, F_1)
        :param acts:   Shape (N_i, ), should be binary or close to it
        :return: Shape (N_j, F_2), where F_2 = F_1/n_heads
        """
        q_tr = self.linear_q(queries)
        k_tr = self.linear_k(keys)
        v_tr = self.linear_v(values)

        q_splits = torch.split(q_tr, self.n_heads, -1)
        k_splits = torch.split(k_tr, self.n_heads, -1)
        v_splits = torch.split(v_tr, self.n_heads, -1)

        heads = []
        for i in range(self.n_heads):
            heads.append(qkv_attention(q_splits[i], k_splits[i], v_splits[i], presence))
        heads = torch.cat(heads, -1)  # (N_j, F_1)

        return self.linear_out(heads)  # (N_j, F_2)


class SetTransformer(nn.Module):
    def __init__(self, n_feats_in, n_caps_out=34, hidden_dim=128, n_heads=1):
        super(SetTransformer, self).__init__()

        self.n_heads = n_heads

        self.vote_transform = nn.Linear(n_feats_in, hidden_dim)

        self.inducing_points = nn.Parameter(torch.zeros(n_caps_out, hidden_dim))
        self.inducing_points.data.normal_(0, 0.5)  # Randomly initializes weights

        self.multihead_qkv_att = MultiHeadQKVAttention(hidden_dim, self.n_heads)

    def forward(self, capsule_poses, capsule_acts):
        """

        :param capsule_poses: Shape (N_i, F_in)
        :param capsule_acts: Shape (N_i, )
        :return: Shape (N_j, F_out)
        """

        # TODO include self attention before qkv attention
        # TODO add functionality for different capsule types, i.e. (C_i, N_i, F_in)

        votes = self.vote_transform(capsule_poses)

        return self.multihead_qkv_att(self.inducing_points, votes, votes, capsule_acts)


class TransformerRouting(nn.Module):
    def __init__(self, n_feats_in, n_caps_out=34, hidden_dim=128, n_heads=1, output_dim=16):
        super(TransformerRouting, self).__init__()

        assert hidden_dim % n_heads == 0

        self.hidden_dim = hidden_dim
        self.n_heads = n_heads
        self.output_dim = output_dim

        self.vote_transform = nn.Linear(n_feats_in, hidden_dim)

        self.inducing_points = nn.Parameter(torch.zeros(n_caps_out, hidden_dim))
        self.inducing_points.data.normal_(0, 0.5)  # Randomly initializes weights

        self.linear_q = nn.Linear(hidden_dim, hidden_dim)
        self.linear_k = nn.Linear(hidden_dim, hidden_dim)
        self.linear_v = nn.Linear(hidden_dim, hidden_dim)

        self.linear_output = nn.Linear(hidden_dim, self.output_dim)

    def forward(self, capsule_poses, capsule_acts):
        """

        :param capsule_poses: Shape (N_i, F_in)
        :param capsule_acts: Shape (N_i, )
        :return: Shape (N_j, F_out), (N_j, )
        """

        # TODO add functionality for different capsule types, i.e. (C_i, N_i, F_in)
        # This would require inputting the votes, and removing this first vote transform

        votes = self.vote_transform(capsule_poses)  # (N_i, F)

        q_tr = self.linear_q(self.inducing_points)  # (N_j, F)
        k_tr = self.linear_k(votes)
        v_tr = self.linear_v(votes)  # (N_i, F) - these are the votes

        q_splits = torch.split(q_tr, self.n_heads, -1)
        k_splits = torch.split(k_tr, self.n_heads, -1)
        v_splits = torch.split(v_tr, self.n_heads, -1)

        heads = []
        for i in range(self.n_heads):
            heads.append(qkv_attention(q_splits[i], k_splits[i], v_splits[i], capsule_acts))
        pred_poses = torch.cat(heads, -1)  # (N_j, F)

        out_poses_res = pred_poses.unsqueeze(1)  # (N_j, 1, F)
        votes_res = v_tr.unsqueeze(0)  # (1, N_i, F)

        diff = (out_poses_res - votes_res)**2  # (N_j, N_i, F)

        acts_res = capsule_acts.unsqueeze(-1).unsqueeze(0)

        sum_acts = torch.sum(acts_res, 1)

        cost_per_dim = torch.sum(diff*acts_res, 1) / (sum_acts + 1e-8)  # (N_j, F)

        total_cost = torch.sum(cost_per_dim, 1) / torch.sqrt(cost_per_dim.shape[-1])  # (N_j, )

        # Ideally, low cost -> high activation
        lmda = 1
        output_activation = torch.softmax(0-total_cost*lmda, -1)  # (N_j, )

        output_poses = self.linear_output(pred_poses)  # (N_j, F_out) # TODO test if independent linear layer is better

        return output_poses, output_activation
