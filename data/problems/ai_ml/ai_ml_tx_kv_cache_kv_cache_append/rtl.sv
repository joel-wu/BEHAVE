module ai_ml_tx_kv_cache_kv_cache_append #(
    parameter int NUM_LAYERS=4,
    parameter int BATCH_SIZE=1,
    parameter int NUM_HEADS=1,
    parameter int HEAD_DIM=8,
    parameter int TOKEN_BLOCK=1,
    parameter int DATA_WIDTH=16,
    parameter int MAX_TOKENS=16,
    parameter int LAYER_IDX_WIDTH=(NUM_LAYERS<=1)?1:$clog2(NUM_LAYERS),
    parameter int SEQ_LEN_WIDTH=16
) (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [LAYER_IDX_WIDTH-1:0] in_layer_idx,
    input logic [BATCH_SIZE*NUM_HEADS*TOKEN_BLOCK*HEAD_DIM*DATA_WIDTH-1:0] in_key_states,
    input logic [BATCH_SIZE*NUM_HEADS*TOKEN_BLOCK*HEAD_DIM*DATA_WIDTH-1:0] in_value_states,
    output logic out_valid,
    input logic out_ready,
    output logic out_append_ok,
    output logic [SEQ_LEN_WIDTH-1:0] out_seq_length,
    output logic [BATCH_SIZE*NUM_HEADS*MAX_TOKENS*HEAD_DIM*DATA_WIDTH-1:0] out_cached_key_states,
    output logic [BATCH_SIZE*NUM_HEADS*MAX_TOKENS*HEAD_DIM*DATA_WIDTH-1:0] out_cached_value_states
);
    localparam int CACHE_BITS=BATCH_SIZE*NUM_HEADS*MAX_TOKENS*HEAD_DIM*DATA_WIDTH;
    logic [CACHE_BITS-1:0] key_cache[0:NUM_LAYERS-1],value_cache[0:NUM_LAYERS-1];
    logic [SEQ_LEN_WIDTH-1:0] length[0:NUM_LAYERS-1];
    logic [CACHE_BITS-1:0] next_keys,next_values;
    logic [SEQ_LEN_WIDTH-1:0] next_length;
    logic next_ok;
    integer selected;

    initial begin
        if (NUM_LAYERS<1 || BATCH_SIZE<1 || NUM_HEADS<1 || HEAD_DIM<1 || TOKEN_BLOCK<1 || DATA_WIDTH<1 || MAX_TOKENS<1 || SEQ_LEN_WIDTH<1)
            $fatal(1,"positive cache dimensions required");
        if (TOKEN_BLOCK>MAX_TOKENS || SEQ_LEN_WIDTH<$clog2(MAX_TOKENS+1) || LAYER_IDX_WIDTH<((NUM_LAYERS<=1)?1:$clog2(NUM_LAYERS)))
            $fatal(1,"cache capacity or layer index does not fit the interface");
    end

    always_comb begin
        selected=int'(in_layer_idx%NUM_LAYERS);
        next_keys=key_cache[selected];next_values=value_cache[selected];
        next_length=length[selected];next_ok=0;
        if (64'(length[selected])+64'(TOKEN_BLOCK)<=64'(MAX_TOKENS)) begin
            for (integer b=0;b<BATCH_SIZE;b=b+1)
                for (integer h=0;h<NUM_HEADS;h=h+1)
                    for (integer t=0;t<TOKEN_BLOCK;t=t+1)
                        for (integer d=0;d<HEAD_DIM;d=d+1) begin
                            next_keys[(((b*NUM_HEADS+h)*MAX_TOKENS+int'(length[selected])+t)*HEAD_DIM+d)*DATA_WIDTH+:DATA_WIDTH]
                                =in_key_states[(((b*NUM_HEADS+h)*TOKEN_BLOCK+t)*HEAD_DIM+d)*DATA_WIDTH+:DATA_WIDTH];
                            next_values[(((b*NUM_HEADS+h)*MAX_TOKENS+int'(length[selected])+t)*HEAD_DIM+d)*DATA_WIDTH+:DATA_WIDTH]
                                =in_value_states[(((b*NUM_HEADS+h)*TOKEN_BLOCK+t)*HEAD_DIM+d)*DATA_WIDTH+:DATA_WIDTH];
                        end
            next_length=length[selected]+SEQ_LEN_WIDTH'(TOKEN_BLOCK);next_ok=1;
        end
    end

    assign in_ready=!out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid<=0;out_append_ok<=0;out_seq_length<=0;
            out_cached_key_states<=0;out_cached_value_states<=0;
            for (integer l=0;l<NUM_LAYERS;l=l+1) begin key_cache[l]<=0;value_cache[l]<=0;length[l]<=0;end
        end else if (in_ready) begin
            out_valid<=in_valid;
            if (in_valid) begin
                if (next_ok) begin key_cache[selected]<=next_keys;value_cache[selected]<=next_values;length[selected]<=next_length;end
                out_append_ok<=next_ok;out_seq_length<=next_length;
                out_cached_key_states<=next_keys;out_cached_value_states<=next_values;
            end else begin
                out_append_ok<=0;out_seq_length<=0;out_cached_key_states<=0;out_cached_value_states<=0;
            end
        end
    end
endmodule
