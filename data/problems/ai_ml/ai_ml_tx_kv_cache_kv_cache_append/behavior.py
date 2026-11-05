class AiMlTxKvCacheKvCacheAppend:
    def __init__(self, num_layers=4, batch_size=1, num_heads=1, head_dim=8,
                 token_block=1, data_width=16, seq_len_width=16, max_tokens=16):
        assert min(num_layers,batch_size,num_heads,head_dim,token_block,data_width,max_tokens,seq_len_width)>=1
        assert token_block<=max_tokens and max_tokens<(1<<seq_len_width)
        self.num_layers=num_layers
        self.batch_size=batch_size
        self.num_heads=num_heads
        self.head_dim=head_dim
        self.token_block=token_block
        self.data_width=data_width
        self.max_tokens=max_tokens
        self.seq_len_width=seq_len_width
        self.reset()

    def reset(self):
        self.key_cache=[0]*self.num_layers
        self.value_cache=[0]*self.num_layers
        self.seq_length=[0]*self.num_layers

    def process(self,in_layer_idx,in_key_states,in_value_states):
        layer=in_layer_idx%self.num_layers
        length=self.seq_length[layer]
        keys=self.key_cache[layer]
        values=self.value_cache[layer]
        accepted=0
        if length+self.token_block<=self.max_tokens:
            mask=(1<<self.data_width)-1
            for b in range(self.batch_size):
                for h in range(self.num_heads):
                    for t in range(self.token_block):
                        for d in range(self.head_dim):
                            src=(((b*self.num_heads+h)*self.token_block+t)*self.head_dim+d)*self.data_width
                            dst=(((b*self.num_heads+h)*self.max_tokens+length+t)*self.head_dim+d)*self.data_width
                            keys=keys|(((in_key_states>>src)&mask)<<dst)
                            values=values|(((in_value_states>>src)&mask)<<dst)
            length=length+self.token_block
            self.key_cache[layer]=keys
            self.value_cache[layer]=values
            self.seq_length[layer]=length
            accepted=1
        return {"out_append_ok":accepted,"out_seq_length":length,
                "out_cached_key_states":keys,"out_cached_value_states":values}
