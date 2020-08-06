import pdb

import torch
import torch.nn.functional as F
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from . import WordEmbeddingBasedNetwork


class BlindStatefulLSTM(WordEmbeddingBasedNetwork):
    """

    Args:
        nn ([type]): [description]
    """
    _HIDDEN_SIZE = 300

    def __init__(self, embedding_path, word2id, num_actions, num_attrs, pad_token, unk_token, seed, OOV_corrections):
        
        torch.manual_seed(seed)
        super(BlindStatefulLSTM, self).__init__(embedding_path, word2id, pad_token, unk_token, OOV_corrections)

        self.num_actions = num_actions
        self.num_attrs = num_attrs
        self.memory_hidden_size = self._HIDDEN_SIZE

        self.history_encoder = nn.LSTM(self.embedding_size, self.memory_hidden_size, batch_first=True, bidirectional=True)
        self.utterance_encoder = nn.LSTM(self.embedding_size, self.memory_hidden_size, batch_first=True, bidirectional=True)
        
        #todo recurrent attention? 
        #self.cross_history_attn = nn.Linear()

        #! this is position agnostic (should not be good)
        self.utterance_memory_attn = nn.Sequential(nn.Linear(4*self.memory_hidden_size, 4*self.memory_hidden_size),
                                                    nn.Tanh(),
                                                    nn.Linear(4*self.memory_hidden_size, 1))
        self.linear_act_post_attn = nn.Sequential(nn.Linear(4*self.memory_hidden_size, 2*self.memory_hidden_size),
                                                    nn.ReLU())
        self.linear_args_post_attn = nn.Sequential(nn.Linear(4*self.memory_hidden_size, 2*self.memory_hidden_size),
                                                    nn.ReLU())

        self.multiturn_actions_outlayer = nn.Linear(in_features=2*self.memory_hidden_size, out_features=self.num_actions)
        self.multiturn_args_outlayer = nn.Linear(in_features=2*self.memory_hidden_size, out_features=self.num_attrs)

        self.singleturn_actions_outlayer = nn.Linear(in_features=2*self.memory_hidden_size, out_features=self.num_actions)
        self.singleturn_args_outlayer = nn.Linear(in_features=2*self.memory_hidden_size, out_features=self.num_attrs)

        #history encoder
        #utterance encoder
        #cross-history attention
        
        #utterance self-attention
        #filtered cross-history attention
        #context encoder
        #action out layer
        #args encoder
        #args out layer

        #self.actions_outlayer = nn.Linear(in_features=self.hidden_size, out_features=num_actions)
        #self.args_outlayer = nn.Linear(in_features=self.hidden_size, out_features=num_args)


    def forward(self, batch, history, seq_lengths=None, device='cpu'):

        # u_t shape [BATCH_SIZE x 2MEMORY_HIDDEN_SIZE]
        u_t = self.encode_utterance(batch, seq_lengths)

        # separate single from multi-turn
        single_turns = []
        single_turns_pos = set()
        multi_turns = []
        multi_turns_history = []
        for dial_idx, history_item in enumerate(history):
            if len(history_item) == 0:
                single_turns_pos.add(dial_idx)
                single_turns.append(u_t[dial_idx])
            else:
                multi_turns.append(u_t[dial_idx])
                multi_turns_history.append(history[dial_idx])
        no_single_turns = True if len(single_turns) == 0 else False
        if not no_single_turns:
            single_turns = torch.stack(single_turns)
            # compute output for single turn dialogues
            act_out1 = self.singleturn_actions_outlayer(single_turns)
            args_out1 = self.singleturn_args_outlayer(single_turns)
        multi_turns = torch.stack(multi_turns)

        # memory bank is a list of BATCH_SIZE tensors, each of them having shape N_TURNSx2MEMORY_HIDDEN_SIZE
        memory_bank = self.encode_history(multi_turns_history, device)        
        assert len(multi_turns) == len(memory_bank), 'Wrong memory size'

        # c_t shape [MULTI_TURNS_SET_SIZE x MEMORY_HIDDEN_SIZE]
        attentive_c_t = self.attention_over_memory(multi_turns, memory_bank)

        #? Hadamard product between c_t and u_t? It is simply "tensor1 * tensor2"
        ut_ct_concat = torch.cat((multi_turns, attentive_c_t), dim=-1)
        c_t_tilde1 = self.linear_act_post_attn(ut_ct_concat)

        ut_ct1_concat = torch.cat((multi_turns, c_t_tilde1), dim=-1)
        c_t_tilde2 = self.linear_args_post_attn(ut_ct1_concat)

        act_out2 = self.multiturn_actions_outlayer(c_t_tilde1)
        args_out2 = self.multiturn_args_outlayer(c_t_tilde2)

        # recompose the output
        act_out = []
        args_out = []
        pos1 = 0
        pos2 = 0
        for idx in range(batch.shape[0]):
            if idx in single_turns_pos:
                act_out.append(act_out1[pos1])
                args_out.append(args_out1[pos1])
                pos1 += 1
            else:
                act_out.append(act_out2[pos2])
                args_out.append(args_out2[pos2])
                pos2 += 1
        act_out = torch.stack(act_out)
        args_out = torch.stack(args_out)

        act_probs = F.softmax(act_out, dim=-1)
        args_probs = torch.sigmoid(args_out)
        return act_out, args_out, act_probs, args_probs


        



    def encode_history(self, history, device):
        #todo turn embedding based on previous turns (hierarchical recurrent encoder - HRE)
        encoded_batch_history = []
        for dial in history:
            hiddens = []
            for turn in dial:
                emb = self.embedding_layer(turn.unsqueeze(0).to(device))
                # h_t.shape = [num_directions x 1 x HIDDEN_SIZE]
                out, (h_t, c_t) = self.history_encoder(emb)
                bidirectional_h_t = torch.cat((h_t[0], h_t[-1]), dim=-1)
                hiddens.append(bidirectional_h_t.squeeze(0))
            assert len(hiddens) > 0, 'Impossible to encode history for single turn instance'
            encoded_batch_history.append(torch.stack(hiddens))
        return encoded_batch_history


    def encode_utterance(self, batch, seq_lengths):
        embedded_seq_tensor = self.embedding_layer(batch)
        if seq_lengths is not None:
            # pack padded sequence
            packed_input = pack_padded_sequence(embedded_seq_tensor, seq_lengths.cpu().numpy(), batch_first=True)
        
        out1, (h_t, c_t) = self.utterance_encoder(packed_input)
        bidirectional_h_t = torch.cat((h_t[0], h_t[-1]), dim=-1)

        """unpack not needed. We don't use the output
        if seq_lengths is not None:
            # unpack padded sequence
            output, input_sizes = pad_packed_sequence(out1, batch_first=True)
        """
        return bidirectional_h_t


    def attention_over_memory(self, u_t, memory_bank):
        # input for attention layer consists of pairs (utterance_j, memory_j_i), for each j, i
        attn_input_list = []
        for dial_idx, dial_mem in enumerate(memory_bank):
            num_turns = dial_mem.shape[0]
            #utterance_mem_concat shape N_TURNS x (utterance_size + memory_size)
            utterance_mem_concat = torch.cat((u_t[dial_idx].expand(num_turns, -1), dial_mem), dim=-1)
            attn_input_list.append(utterance_mem_concat)

        scores = []
        for idx, input_tensor in enumerate(attn_input_list):
            curr_out = self.utterance_memory_attn(input_tensor)
            scores.append(curr_out)

        attn_weights = []
        for score in scores:
            attn = F.softmax(score, dim=0)
            attn_weights.append(attn)

        assert len(attn_weights) == len(memory_bank), 'Memory size and attention weights do not match'
        weighted_sum_list = []
        for attn, mem in zip(attn_weights, memory_bank):
            weighted_mem = attn * mem
            weighted_sum = torch.sum(weighted_mem, dim=0)
            weighted_sum_list.append(weighted_sum)
        weighted_sum = torch.stack(weighted_sum_list)

        return weighted_sum


    def collate_fn(self, batch):
        """This method prepares the batch for the LSTM: padding + preparation for pack_padded_sequence

        Args:
            batch (tuple): tuple of element returned by the Dataset.__getitem__()

        Returns:
            dial_ids (list): list of dialogue ids
            turns (list): list of dialogue turn numbers
            seq_tensor (torch.LongTensor): tensor with BxMAX_SEQ_LEN containing padded sequences of user transcript sorted by descending effective lengths
            seq_lenghts: tensor with shape B containing the effective length of the correspondant transcript sequence
            actions (torch.Longtensor): tensor with B shape containing target actions
            arguments (torch.Longtensor): tensor with Bx33 shape containing arguments one-hot vectors, one for each sample.
        """
        dial_ids = [item[0] for item in batch]
        turns = [item[1] for item in batch]
        history = [item[3] for item in batch]
        actions = torch.tensor([item[4] for item in batch])
        arguments = torch.tensor([item[5] for item in batch])

        # words to ids for the history
        history_seq_ids = []
        for turn, item in zip(turns, history):
            assert len(item) == turn, 'Number of turns does not match history length'
            curr_turn_ids = []
            for t in range(turn):
                concat_sentences = item[t][0] + ' ' + item[t][1] #? separator token
                ids = torch.tensor([self.word2id[word] for word in concat_sentences.split()])
                curr_turn_ids.append(ids)
            history_seq_ids.append(curr_turn_ids)

        # words to ids for the current utterance
        utterance_seq_ids = []
        for item in batch:
            curr_seq = []
            for word in item[2].split():
                if word in self.word2id:
                    curr_seq.append(self.word2id[word])
                else:
                    curr_seq.append(self.word2id[self.unk_token])
            utterance_seq_ids.append(curr_seq)
        
        seq_lengths = torch.tensor(list(map(len, utterance_seq_ids)), dtype=torch.long)
        seq_tensor = torch.zeros((len(utterance_seq_ids), seq_lengths.max()), dtype=torch.long)

        for idx, (seq, seqlen) in enumerate(zip(utterance_seq_ids, seq_lengths)):
            seq_tensor[idx, :seqlen] = torch.tensor(seq, dtype=torch.long)

        # sort instances by sequence length in descending order
        seq_lengths, perm_idx = seq_lengths.sort(0, descending=True)

        # reorder the sequences from the longest one to the shortest one.
        # keep the correspondance with the target
        seq_tensor = seq_tensor[perm_idx]
        actions = actions[perm_idx]
        arguments = arguments[perm_idx]

        # seq_lengths is used to create a pack_padded_sequence
        return dial_ids, turns, seq_tensor, seq_lengths, history_seq_ids, actions, arguments


    def __str__(self):
        return super().__str__()
