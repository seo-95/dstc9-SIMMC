import math
import pdb

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

from .embednets import ItemEmbeddingNetwork, WordEmbeddingNetwork
from .decoder import Decoder
from .old_encoder import SingleEncoder
from .bert import BertEncoder
from transformers import BertTokenizer #todo remove

_MAX_INFER_LEN = 30

class MMStatefulLSTM(nn.Module):

    def __init__(self,
                pad_token,
                start_token,
                end_token,
                unk_token, #? remove
                seed,
                dropout_prob,
                n_decoders,
                decoder_heads,
                out_vocab,
                freeze_bert=False,
                beam_size=None,
                retrieval_eval=False,
                mode='train',
                device='cpu'):

        torch.manual_seed(seed)
        super(MMStatefulLSTM, self).__init__()

        if mode == 'inference':
            assert beam_size is not None, 'Beam size need to be defined during inference'

        self.mode = mode
        self.beam_size = beam_size
        self.retrieval_eval = retrieval_eval
        self.bert2genid = out_vocab
        
        #self.item_embeddings_layer = ItemEmbeddingNetwork(item_embeddings_path)
        """
        self.word_embeddings_layer = WordEmbeddingNetwork(word_embeddings_path=word_embeddings_path, 
                                                        word2id=word2id, 
                                                        pad_token=pad_token, 
                                                        unk_token=unk_token,
                                                        freeze=freeze_embeddings)
        self.emb_dim = self.word_embeddings_layer.embedding_size
        """

        self.bert = BertEncoder(pretrained='bert-base-uncased', freeze=freeze_bert)
        self.tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        self.vocab = self.tokenizer.vocab
        self.id2word = {id: word for word, id in self.vocab.items()}
        self.genid2word = {gen_id: self.tokenizer.convert_ids_to_tokens(bert_id) for bert_id, gen_id in self.bert2genid.items()}
        self.genid2bertid = {id: word for word, id in self.bert2genid.items()}
        #start_id only presents in input
        self.start_id = self.vocab[start_token]
        #end_id only presents in output
        self.end_id = self.bert2genid[self.vocab[end_token]]
        #pad_id is the same between input and ouput
        self.pad_id = self.vocab[pad_token]
        self.unk_id = self.vocab[unk_token] 
        conf = self.bert.configuration
        self.input_vocab_size = conf.vocab_size
        self.output_vocab_size = len(out_vocab)
        self.encoder_hidden_size = conf.hidden_size
        """
        self.encoder = SingleEncoder(input_size=self.emb_dim,
                                    hidden_size=hidden_size,
                                    dropout_prob=dropout_prob,
                                    encoder_heads=encoder_heads,
                                    embedding_net=self.word_embeddings_layer)
        """

        #for h heads: d_k == d_v == emb_dim/h
        self.decoder = Decoder(d_model=self.encoder_hidden_size,
                                d_enc=self.encoder_hidden_size,
                                d_context=self.encoder_hidden_size,
                                d_k=self.encoder_hidden_size//decoder_heads,
                                d_v=self.encoder_hidden_size//decoder_heads,
                                d_f=self.encoder_hidden_size//2,
                                n_layers=n_decoders,
                                n_heads=decoder_heads,
                                embedding_net=self.bert,
                                input_vocab_size=self.input_vocab_size,
                                out_vocab_size=self.output_vocab_size,
                                dropout_prob=dropout_prob)


    def forward(self,
                utterances,
                utterances_mask,
                utterances_token_type,
                responses,
                responses_mask,
                responses_token_type,
                focus,
                focus_mask,
                focus_token_type,
                history,
                actions,
                attributes,
                candidates=None,
                candidates_mask=None,
                candidates_token_type=None,
                candidates_targets=None,
                seq_lengths=None):
        """The visual context is a list of visual contexts (a batch). Each visual context is, in turn, a list
            of items. Each item is a list of (key, values) pairs, where key is a tensor containing the word ids
            for the field name and values is a list of values where each value is a tensor of word ids.

        type(visual_context[<sample_in_batch>][<item_n>][<field_n>]) -> tuple(tensor(key), [tensor(value)])

        Args:
            utterances ([type]): [description]
            history ([type]): [description]
            visual_context ([type]): [description]
            seq_lengths ([type], optional): [description]. Defaults to None.
            device (str, optional): [description]. Defaults to 'cpu'.

        Returns:
            [type]: [description]
        """
        curr_device = utterances.device
        if self.mode == 'inference':
            assert utterances.shape[0] == 1, 'Only unitary batches allowed during inference'
        #check batch size consistency (especially when using different gpus) and move list tensors to correct gpu
        assert utterances.shape[0] == utterances_mask.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == utterances_token_type.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == responses.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == responses_mask.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == responses_token_type.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == focus.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == focus_mask.shape[0], 'Inconstistent batch size'
        assert utterances.shape[0] == focus_token_type.shape[0], 'Inconstistent batch size'
        if self.retrieval_eval:
            assert candidates_targets is not None, 'Candidates have to be specified with retrieval_eval set to True'

        """
        assert utterances.shape[0] == len(history), 'Inconsistent batch size'
        assert utterances.shape[0] == len(actions), 'Inconsistent batch size'
        assert utterances.shape[0] == len(attributes), 'Inconsistent batch size'
        assert utterances.shape[0] == len(focus_items), 'Inconsistent batch size'
        assert utterances.shape == utterances_mask.shape, 'Inconsistent mask size'
        assert candidates_pool.shape == pools_padding_mask.shape, 'Inconsistent mask size'
        curr_device = utterances.device
        for idx, _ in enumerate(history):
            if len(history[idx]):
                history[idx] = history[idx].to(curr_device)
        """

        u_t_all = self.bert(input=utterances,
                            input_mask=utterances_mask,
                            input_token_type=utterances_token_type)
        v_t_tilde = self.bert(input=focus,
                            input_mask=focus_mask,
                            input_token_type=focus_token_type)
        """
        u_t_all, v_t_tilde, h_t_tilde = self.encoder(utterances=utterances,
                                                    history=history,
                                                    focus_items=focus,
                                                    seq_lengths=seq_lengths)
        """
        #decoding phase
        if self.mode == 'train':
            vocab_logits = self.decoder(input_batch=responses,
                                        encoder_out=u_t_all,
                                        #history_context=h_t_tilde,
                                        visual=v_t_tilde,
                                        input_mask=responses_mask,
                                        enc_mask=utterances_mask,
                                        visual_mask=focus_mask)
            """
            response_losses = self.response_criterion(vocab_logits.view(vocab_logits.shape[0]*vocab_logits.shape[1], -1), 
                                                    generative_targets.view(vocab_logits.shape[0]*vocab_logits.shape[1]))
            """
            return vocab_logits
        else:
            #at inference time (NOT EVAL)
            self.never_ending = 0
            dec_args = {'encoder_out': u_t_all, 'enc_mask': utterances_mask, 'visual': v_t_tilde, 'visual_mask': focus_mask}
            #dec_args = {'encoder_out': u_t_all, 'history_context': h_t_tilde, 'visual_context': v_t_tilde, 'enc_mask': utterances_mask}
            best_dict = self.beam_search(curr_seq=[self.start_id],
                                        curr_score=0,
                                        dec_args=dec_args,
                                        best_dict={'seq': [], 'score': -float('inf')},
                                        device=curr_device)
            best_dict['string'] = self.tokenizer.decode([self.genid2bertid[id] for id in best_dict['seq']])
            #print('Never-ending generated sequences: {}'.format(self.never_ending))
            infer_res = (best_dict,)
            if self.retrieval_eval:
                #eval on retrieval task 
                #build a fake batch by expanding the tensors
                #pdb.set_trace()
                vocab_logits = [
                                    self.decoder(input_batch=pool,
                                                encoder_out=u_t_all.expand(pool.shape[0], -1, -1),
                                                #history_context=h_t_tilde.expand(pool.shape[0], -1),
                                                visual=v_t_tilde.expand(pool.shape[0], -1, -1),
                                                visual_mask=focus_mask.expand(pool.shape[0], -1),
                                                input_mask=pool_mask,
                                                enc_mask=utterances_mask.expand(pool.shape[0], -1))
                                    for pool, pool_mask in zip(candidates, candidates_mask)
                                ]
                #candidates_scores shape: Bx100
                candidates_scores = self.compute_candidates_scores(candidates_targets, vocab_logits)
                infer_res += (candidates_scores,)
            return infer_res

    
    def beam_search(self, curr_seq, curr_score, dec_args, best_dict, device):
        #pdb.set_trace()
        if curr_seq[-1] == self.end_id or len(curr_seq) > _MAX_INFER_LEN:
            assert len(curr_seq)-1 != 0, 'Division by 0 for generated sequence {}'.format(curr_seq)
            #discard the start_id only. The probability of END token has to be taken into account instead.
            norm_score = curr_score/(len(curr_seq)-1)
            if norm_score > best_dict['score']:
                best_dict['score'], best_dict['seq'] = curr_score, curr_seq[1:].copy() #delete the start_token
            if len(curr_seq) > _MAX_INFER_LEN:
                self.never_ending += 1
            return best_dict
        vocab_logits = self.decoder(input_batch=torch.tensor(curr_seq).unsqueeze(0).to(device), **dec_args).squeeze(0)
        #take only the prediction for the last word
        vocab_logits = vocab_logits[-1]
        beam_ids = torch.argsort(vocab_logits, descending=True, dim=-1)[:self.beam_size].tolist()
        lprobs = F.log_softmax(vocab_logits, dim=-1)
        for curr_id in beam_ids:
            curr_lprob = lprobs[curr_id].item()
            best_dict = self.beam_search(curr_seq=curr_seq+[curr_id], 
                                        curr_score=curr_score+curr_lprob, 
                                        dec_args=dec_args, 
                                        best_dict=best_dict, 
                                        device=device)
        return best_dict


    def compute_candidates_scores(self, candidates_targets, vocab_logits):
        """The score of each candidate is the sum of the log-likelihood of each word, normalized by its length.
        The score will be a negative value, longer sequences will be penalized without the normalization by length.
        """
        scores = torch.zeros(candidates_targets.shape[:2])
        for batch_idx, (pool_ids, pool_logits) in enumerate(zip(candidates_targets, vocab_logits)):
            pool_lprobs = F.log_softmax(pool_logits, dim=-1)
            for sentence_idx, (candidate_ids, candidate_lprobs) in enumerate(zip(pool_ids, pool_lprobs)):
                curr_lprob = []
                for candidate_word, words_probs in zip(candidate_ids, candidate_lprobs):
                    #until padding
                    if candidate_word.item() == self.pad_id:
                        break
                    curr_lprob.append(words_probs[candidate_word.item()].item())
                scores[batch_idx, sentence_idx] = sum(curr_lprob)/len(curr_lprob)
        return scores


    def collate_fn(self, batch):
        """
        This method prepares the batch for the LSTM: padding + preparation for pack_padded_sequence

        Args:
            batch (tuple): tuple of element returned by the Dataset.__getitem__()

        Returns:
            dial_ids (list): list of dialogue ids
            turns (list): list of dialogue turn numbers
            seq_tensor (torch.LongTensor): tensor with BxMAX_SEQ_LEN containing padded sequences of user transcript sorted by descending effective lengths
            seq_lenghts: tensor with shape B containing the effective length of the correspondant transcript sequence
            actions (torch.Longtensor): tensor with B shape containing target actions
            attributes (torch.Longtensor): tensor with Bx33 shape containing attributes one-hot vectors, one for each sample.
        """
        dial_ids = [item[0] for item in batch]
        turns = [item[1] for item in batch]
        utterances = torch.stack([item[2] for item in batch])
        utterances_mask = torch.stack([item[3] for item in batch])
        utterances_token_type = torch.stack([item[4] for item in batch])
        responses = torch.stack([item[5] for item in batch])
        responses_mask = torch.stack([item[6] for item in batch])
        responses_token_type = torch.stack([item[7] for item in batch])
        #history = [item[4] for item in batch]
        #actions = torch.stack([item[5] for item in batch])
        #attributes = [item[6] for item in batch]
        focus = torch.stack([item[8] for item in batch])
        focus_mask = torch.stack([item[9] for item in batch])
        focus_token_type = torch.stack([item[10] for item in batch])
        if self.mode == 'train':
            #creates target by shifting and converting id to output vocabulary
            generative_targets = torch.cat((responses[:, 1:].clone().detach(), torch.zeros((responses.shape[0], 1), dtype=torch.long)), dim=-1)
            for batch_idx, _ in enumerate(generative_targets):
                for id_idx, curr_id in enumerate(generative_targets[batch_idx]):
                    if curr_id.item() == self.pad_id:
                        continue
                    if curr_id.item() not in self.bert2genid:
                        #dev test contains oov tokens
                        generative_targets[batch_idx][id_idx] = self.bert2genid[self.unk_id]
                    else:
                        generative_targets[batch_idx][id_idx] = self.bert2genid[curr_id.item()]
        if self.mode == 'inference' and self.retrieval_eval:
            candidates = torch.stack([item[11] for item in batch])
            candidates_mask = torch.stack([item[12] for item in batch])
            candidates_token_type = torch.stack([item[13] for item in batch])

            candidates_targets = torch.cat((candidates[:, :, 1:].clone().detach(), torch.zeros((responses.shape[0], 100, 1), dtype=torch.long)), dim=-1)
            for batch_idx, _ in enumerate(candidates_targets):
                for pool_idx, curr_pool in enumerate(candidates_targets[batch_idx]):
                    for id_idx, curr_id in enumerate(candidates_targets[batch_idx][pool_idx]):
                        if curr_id.item() == self.pad_id:
                            continue
                        if curr_id.item() not in self.bert2genid:
                            candidates_targets[batch_idx][pool_idx][id_idx] = self.bert2genid[self.unk_id]
                        else:
                            candidates_targets[batch_idx][pool_idx][id_idx] = self.bert2genid[curr_id.item()]
        assert utterances.shape[0] == len(dial_ids), 'Batch sizes do not match'
        assert utterances.shape[0] == len(turns), 'Batch sizes do not match'
        #assert len(utterances) == len(history), 'Batch sizes do not match'
        #assert len(utterances) == len(actions), 'Batch sizes do not match'
        #assert len(utterances) == len(attributes), 'Batch sizes do not match'
        assert utterances.shape[0] == len(focus), 'Batch sizes do not match'
        if self.mode == 'train':
            assert responses.shape == generative_targets.shape, 'Batch sizes do not match'
        if self.mode == 'inference' and self.retrieval_eval:
            assert len(utterances) == candidates.shape[0], 'Batch sizes do not match'


        batch_dict = {}
        batch_dict['utterances'] = utterances
        batch_dict['utterances_mask'] = utterances_mask
        batch_dict['utterances_token_type'] = utterances_token_type
        batch_dict['responses'] = responses
        batch_dict['responses_mask'] = responses_mask
        batch_dict['responses_token_type'] = responses_token_type
        batch_dict['focus'] = focus
        batch_dict['focus_mask'] = focus_mask
        batch_dict['focus_token_type'] = focus_token_type
        if self.retrieval_eval:
            batch_dict['candidates'] = candidates
            batch_dict['candidates_mask'] = candidates_mask
            batch_dict['candidates_token_type'] = candidates_token_type
            batch_dict['candidates_targets'] = candidates_targets
        """
        # reorder the sequences from the longest one to the shortest one.
        # keep the correspondance with the target
        transcripts_lengths = torch.tensor(list(map(len, utterances)), dtype=torch.long)
        transcripts_tensor = torch.zeros((len(utterances), transcripts_lengths.max()), dtype=torch.long)
        transcripts_padding_mask = torch.zeros((len(utterances), transcripts_lengths.max()), dtype=torch.long)
        for idx, (seq, seqlen) in enumerate(zip(utterances, transcripts_lengths)):
            transcripts_tensor[idx, :seqlen] = seq.clone().detach()
            transcripts_padding_mask[idx, :seqlen] = 1

        #pad the history
        padded_history = []
        for history_sample in history:
            if not len(history_sample):
                padded_history.append(history_sample)
                continue
            history_lens = torch.tensor(list(map(len, history_sample)), dtype=torch.long)
            history_tensor = torch.zeros((len(history_sample), history_lens.max()), dtype=torch.long)
            for idx, (seq, seqlen) in enumerate(zip(history_sample, history_lens)):
                history_tensor[idx, :seqlen] = seq.clone().detach()
            padded_history.append(history_tensor)

        #pad the response candidates
        # if training take only the true response (the first one)
        if self.training or not self.retrieval_eval:
            responses_pool = [pool_sample[0] for pool_sample in responses_pool]
            batch_lens = torch.tensor(list(map(len, responses_pool)), dtype=torch.long)
            pools_tensor = torch.zeros((len(responses_pool), batch_lens.max()+2), dtype=torch.long)
            pools_padding_mask = torch.zeros((len(responses_pool), batch_lens.max()+2), dtype=torch.long)
            pools_tensor[:, 0] = self.start_id
            for batch_idx, (seq, seqlen) in enumerate(zip(responses_pool, batch_lens)):
                pools_tensor[batch_idx, 1:seqlen+1] = seq.clone().detach()
                pools_tensor[batch_idx, seqlen+1] = self.end_id
                pools_padding_mask[batch_idx, :seqlen+2] = 1
        else:
            batch_lens = torch.tensor([list(map(len, pool_sample)) for pool_sample in responses_pool], dtype=torch.long)
            pools_tensor = torch.zeros((len(responses_pool), len(responses_pool[0]), batch_lens.max()+2), dtype=torch.long)
            pools_padding_mask = torch.zeros((len(responses_pool), len(responses_pool[0]), batch_lens.max()+2), dtype=torch.long)
            pools_tensor[:, :, 0] = self.start_id
            for batch_idx, (pool_lens, pool_sample) in enumerate(zip(batch_lens, responses_pool)):       
                for pool_idx, (seq, seqlen) in enumerate(zip(pool_sample, pool_lens)):
                    pools_tensor[batch_idx, pool_idx, 1:seqlen+1] = seq.clone().detach()
                    pools_tensor[batch_idx, pool_idx, seqlen+1] = self.end_id
                    pools_padding_mask[batch_idx, pool_idx, :seqlen+2] = 1

        #pad focus items
        padded_focus = []
        keys = [[datum[0] for datum in item] for item in focus_items]
        vals = [[datum[1] for datum in item] for item in focus_items]
        batch_klens = [list(map(len, key)) for key in keys]
        batch_vlens = [list(map(len, val)) for val in vals]
        klens_max, vlens_max = 0, 0
        for klens, vlens in zip(batch_klens, batch_vlens):
            curr_kmax = max(klens)
            curr_vmax = max(vlens)
            klens_max = curr_kmax if curr_kmax > klens_max else klens_max
            vlens_max = curr_vmax if curr_vmax > vlens_max else vlens_max

        for batch_idx, item in enumerate(focus_items):
            curr_keys = torch.zeros((len(item), klens_max), dtype=torch.long)
            curr_vals = torch.zeros((len(item), vlens_max), dtype=torch.long)
            for item_idx, (k, v) in enumerate(item):
                curr_keys[item_idx, :batch_klens[batch_idx][item_idx]] = k.clone().detach()
                curr_vals[item_idx, :batch_vlens[batch_idx][item_idx]] = v.clone().detach()
            padded_focus.append([curr_keys, curr_vals])

        
        # sort instances by sequence length in descending order and order targets to keep the correspondance
        transcripts_lengths, perm_idx = transcripts_lengths.sort(0, descending=True)
        transcripts_tensor = transcripts_tensor[perm_idx]
        transcripts_padding_mask = transcripts_padding_mask[perm_idx]
        pools_tensor = pools_tensor[perm_idx]
        pools_padding_mask = pools_padding_mask[perm_idx]
        sorted_dial_ids = []
        sorted_dial_turns = []
        sorted_dial_history = []
        sorted_actions = []
        sorted_attributes = []
        sorted_focus_items = []
        for idx in perm_idx:
            sorted_dial_ids.append(dial_ids[idx])
            sorted_dial_turns.append(turns[idx])
            sorted_dial_history.append(padded_history[idx])
            sorted_actions.append(actions[idx])
            sorted_attributes.append(attributes[idx])
            sorted_focus_items.append(padded_focus[idx])
        

        batch_dict = {}
        batch_dict['utterances'] = transcripts_tensor
        batch_dict['utterances_mask'] = transcripts_padding_mask
        batch_dict['history'] = padded_history
        batch_dict['actions'] = actions
        batch_dict['attributes'] = attributes
        batch_dict['focus_items'] = padded_focus
        #batch_dict['seq_lengths'] = transcripts_lengths
        """
        ret_tuple = (dial_ids, turns, batch_dict)
        if self.mode == 'train':
            ret_tuple += (generative_targets,)
        return ret_tuple


    def __str__(self):
        return super().__str__()







