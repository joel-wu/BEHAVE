module Sparse24Pattern (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [511:0] in_weights,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_mask,
    output logic [511:0] out_dense,
    output logic [255:0] out_values,
    output logic [31:0] out_indices
);
    logic [31:0] next_out_mask;
    logic [511:0] next_out_dense;
    logic [255:0] next_out_values;
    logic [31:0] next_out_indices;
    integer signed_values[0:3], magnitude[0:3];
    logic [15:0] words[0:3];
    integer rank,slot;
    always_comb begin
        next_out_mask=0;next_out_dense=0;next_out_values=0;next_out_indices=0;
        rank=0;slot=0;
        for(int g=0;g<8;g++) begin
            for(int j=0;j<4;j++) begin
                words[j]=in_weights[16*(4*g+j) +:16];
                signed_values[j]=$signed(words[j]);
                magnitude[j]=signed_values[j]<0 ? -signed_values[j] : signed_values[j];
            end
            slot=0;
            for(int j=0;j<4;j++) begin
                rank=0;
                for(int k=0;k<4;k++) begin
                    if(magnitude[k]>magnitude[j] || (magnitude[k]==magnitude[j] && k<j)) rank=rank+1;
                end
                if(rank<2) begin
                    next_out_mask[4*g+j]=1;
                    next_out_dense[16*(4*g+j) +:16]=words[j];
                    next_out_values[16*(2*g+slot) +:16]=words[j];
                    next_out_indices[2*(2*g+slot) +:2]=2'(j);
                    slot=slot+1;
                end
            end
        end
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_mask <= 0; out_dense <= 0; out_values <= 0; out_indices <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_mask <= next_out_mask; out_dense <= next_out_dense; out_values <= next_out_values; out_indices <= next_out_indices; end
            else begin out_mask <= 0; out_dense <= 0; out_values <= 0; out_indices <= 0; end
        end
    end
endmodule
