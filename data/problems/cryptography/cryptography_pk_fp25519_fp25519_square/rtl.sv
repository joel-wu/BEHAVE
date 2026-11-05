module cryptography_pk_fp25519_fp25519_square (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_z
);

    localparam logic [255:0] P = 256'h7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed;
    localparam logic [255:0] PM19 = 256'h7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffda;

    logic busy;

    logic [255:0] x_red;
    logic [511:0] sq;
    logic [254:0] lo1;
    logic [256:0] hi1;
    logic [261:0] fold1;
    logic [254:0] lo2;
    logic [6:0]   hi2;
    logic [255:0] fold2;
    logic [255:0] z_red;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        if (in_x[255] == 1'b0) begin
            if (in_x >= P) begin
                x_red = in_x - P;
            end else begin
                x_red = in_x;
            end
        end else begin
            x_red = {1'b0, in_x[254:0]} + 256'd19;
        end
    end

    always_comb begin
        sq = x_red * x_red;

        lo1   = sq[254:0];
        hi1   = {2'b00, sq[511:255]};
        fold1 = {7'b0, lo1} + (hi1 * 7'd19);

        lo2   = fold1[254:0];
        hi2   = fold1[261:255];
        fold2 = {1'b0, lo2} + (hi2 * 256'd19);

        if (fold2 >= P) begin
            z_red = fold2 - P;
        end else begin
            z_red = fold2;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_z <= 256'b0;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_z <= z_red;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule