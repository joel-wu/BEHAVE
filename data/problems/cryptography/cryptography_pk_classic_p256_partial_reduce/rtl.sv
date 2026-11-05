module cryptography_pk_classic_p256_partial_reduce (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [511:0] in_x,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_r
);

    localparam logic [255:0] P256 = 256'hFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF;

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic [255:0] reduced_r;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [255:0] step_mod(
        input logic [255:0] rem_in,
        input logic         next_bit
    );
        logic [256:0] tmp;
        begin
            tmp = {rem_in, next_bit};
            if (tmp >= {1'b0, P256}) step_mod = tmp[255:0] - P256;
            else                     step_mod = tmp[255:0];
        end
    endfunction

    function automatic logic [255:0] mod_p256_512(input logic [511:0] x);
        logic [255:0] remainder;
        begin
            remainder = '0;
            for (int bit_index = 511; bit_index >= 0; bit_index--)
                remainder = step_mod(remainder, x[bit_index]);
            mod_p256_512 = remainder;
        end
    endfunction

    always_comb begin
        reduced_r = mod_p256_512(in_x);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy  <= 1'b0;
            out_r <= 256'd0;
        end else if (input_accepted) begin
            out_r <= reduced_r;
            busy  <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
