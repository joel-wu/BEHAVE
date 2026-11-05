module poseidon_sbox (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [253:0] in_in,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [253:0] out_out
);

    localparam logic [253:0] P =
        254'd21888242871839275222246405745257275088548364400416034343698204186575808495617;

    logic busy;
    logic [253:0] out_out_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [253:0] mod_reduce_254(input logic [253:0] value);
        logic [254:0] rem;
        integer i;
        begin
            rem = 255'd0;
            for (i = 253; i >= 0; i = i - 1) begin
                rem = {rem[253:0], value[i]};
                if (rem >= {1'b0, P}) begin
                    rem = rem - {1'b0, P};
                end
            end
            mod_reduce_254 = rem[253:0];
        end
    endfunction

    function automatic logic [253:0] mod_reduce_508(input logic [507:0] value);
        logic [254:0] rem;
        integer i;
        begin
            rem = 255'd0;
            for (i = 507; i >= 0; i = i - 1) begin
                rem = {rem[253:0], value[i]};
                if (rem >= {1'b0, P}) begin
                    rem = rem - {1'b0, P};
                end
            end
            mod_reduce_508 = rem[253:0];
        end
    endfunction

    always_comb begin
        logic [253:0] x_red;
        logic [253:0] in2_red;
        logic [253:0] in4_red;
        logic [507:0] mul1;
        logic [507:0] mul2;
        logic [507:0] mul3;

        x_red = mod_reduce_254(in_in);

        mul1 = x_red * x_red;
        in2_red = mod_reduce_508(mul1);

        mul2 = in2_red * in2_red;
        in4_red = mod_reduce_508(mul2);

        mul3 = in4_red * x_red;
        out_out_next = mod_reduce_508(mul3);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy    <= 1'b0;
            out_out <= 254'd0;
        end else if (input_accepted) begin
            busy    <= 1'b1;
            out_out <= out_out_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule