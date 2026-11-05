module AiMlQuantKvPagedQuantKvBlock #(
    parameter int NUM_BLOCKS=4, NUM_HEADS=2, HEAD_SIZE=8, BLOCK_SIZE=4,
    parameter int X=4, ELEM_W=16, SLOT_W=32
) (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [1:0] in_op,
    input logic [SLOT_W-1:0] in_slot,
    input logic [NUM_HEADS*HEAD_SIZE*ELEM_W-1:0] in_key, in_value,
    input logic [31:0] in_address,
    output logic out_valid,
    input logic out_ready,
    output logic out_ok, out_wrote,
    output logic [ELEM_W-1:0] out_data
);
    localparam int WORDS=NUM_BLOCKS*NUM_HEADS*HEAD_SIZE*BLOCK_SIZE;
    initial begin
        if (NUM_BLOCKS<1 || NUM_HEADS<1 || HEAD_SIZE<1 || BLOCK_SIZE<1 || X<1 ||
            HEAD_SIZE%X!=0 || ELEM_W<1 || ELEM_W>32 || SLOT_W<2 || SLOT_W>32 ||
            NUM_BLOCKS*BLOCK_SIZE >= (64'd1 << (SLOT_W-1)) || WORDS>4096)
            $fatal(1,"invalid cache shape");
    end
    logic [ELEM_W-1:0] key_cache[0:WORDS-1], value_cache[0:WORDS-1];
    integer block_index, block_offset, ka, va, source;
    assign in_ready=!out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            out_valid<=0;out_ok<=0;out_wrote<=0;out_data<=0;
            for (int i=0;i<WORDS;i++) begin key_cache[i]<=0;value_cache[i]<=0;end
        end else if (in_ready) begin
            out_valid<=in_valid;out_ok<=0;out_wrote<=0;out_data<=0;
            if (in_valid) begin
                case(in_op)
                    0: begin
                        if (!in_slot[SLOT_W-1] && $unsigned(in_slot)<NUM_BLOCKS*BLOCK_SIZE) begin
                            block_index=int'($unsigned(in_slot))/BLOCK_SIZE;
                            block_offset=int'($unsigned(in_slot))%BLOCK_SIZE;
                            for (int h=0;h<NUM_HEADS;h++) begin
                                for (int e=0;e<HEAD_SIZE;e++) begin
                                    source=h*HEAD_SIZE+e;
                                    ka=((((block_index*NUM_HEADS+h)*(HEAD_SIZE/X)+e/X)*BLOCK_SIZE+block_offset)*X+e%X);
                                    va=((block_index*NUM_HEADS+h)*HEAD_SIZE+e)*BLOCK_SIZE+block_offset;
                                    key_cache[ka]<=in_key[source*ELEM_W +: ELEM_W];
                                    value_cache[va]<=in_value[source*ELEM_W +: ELEM_W];
                                end
                            end
                            out_ok<=1;out_wrote<=1;
                        end
                    end
                    1: if (in_address<WORDS) begin out_ok<=1;out_data<=key_cache[in_address];end
                    2: if (in_address<WORDS) begin out_ok<=1;out_data<=value_cache[in_address];end
                    default: begin end
                endcase
            end
        end
    end
endmodule
