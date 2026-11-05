module SetBitStreamCalculator #(parameter int p_max_set_bit_count_width = 8) (
    input logic i_clk, i_rst_n, i_ready, i_bit_in,
    output logic [p_max_set_bit_count_width-1:0] o_set_bit_count
);
    logic previous_ready;
    always_ff @(posedge i_clk or negedge i_rst_n) begin
        if (!i_rst_n) begin o_set_bit_count<='0; previous_ready<=0; end
        else begin
            previous_ready<=i_ready;
            if (i_ready && !previous_ready) o_set_bit_count<='0;
            else if (i_ready && i_bit_in && !(&o_set_bit_count))
                o_set_bit_count<=o_set_bit_count+1'b1;
        end
    end
endmodule
