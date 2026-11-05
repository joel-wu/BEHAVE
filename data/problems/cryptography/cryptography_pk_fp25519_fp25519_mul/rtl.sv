module FP25519Mul (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_x,
    input  logic [255:0] in_y,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_z
);

    localparam logic [255:0] P = 256'h7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed;

    logic busy;
    logic [255:0] z_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [255:0] mod_p_256(input logic [255:0] a);
        logic [255:0] t;
        begin
            if (a[255] == 1'b0) begin
                if (a >= P) t = a - P;
                else        t = a;
            end else begin
                t = {1'b0, a[254:0]} + 256'd19;
                if (t >= P) t = t - P;
            end
            mod_p_256 = t;
        end
    endfunction

    function automatic logic [255:0] mul_mod_p(input logic [255:0] x_in, input logic [255:0] y_in);
        logic [255:0] x;
        logic [255:0] y;
        logic [255:0] a;
        logic [255:0] res;
        integer i;
        begin
            x   = x_in;
            y   = y_in;
            res = 256'd0;

            for (i = 0; i < 255; i = i + 1) begin
                if (y[i]) begin
                    a = res + x;
                    if (a >= P) res = a - P;
                    else        res = a;
                end

                if (x >= (P - x)) x = x + x - P;
                else              x = x + x;
            end

            mul_mod_p = res;
        end
    endfunction

    always_comb begin
        z_next = mul_mod_p(mod_p_256(in_x), mod_p_256(in_y));
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy  <= 1'b0;
            out_z <= 256'd0;
        end else if (input_accepted) begin
            out_z <= z_next;
            busy  <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule