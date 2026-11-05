module data_systems_vector_search_lsh_signature_pack #(
    parameter int D = 32,
    parameter int NBITS = 32
) (
    input  logic                               clk,
    input  logic                               rst,

    input  logic                               in_valid,
    output logic                               in_ready,
    input  logic [D*32-1:0]                    in_x,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [((NBITS + 7) / 8) * 8 - 1:0] out_bytes
);

    localparam int OUT_W = ((NBITS + 7) / 8) * 8;

    logic busy;
    logic [OUT_W-1:0] packed_sig;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;
    assign out_bytes = packed_sig;

    integer i;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy       <= 1'b0;
            packed_sig <= '0;
        end else if (input_accepted) begin
            packed_sig <= '0;
            for (i = 0; i < NBITS; i = i + 1) begin
                packed_sig[i] <= (in_x[32*i +: 31] <= 31'h7F800000)
                    && (!in_x[32*i + 31] || in_x[32*i +: 31] == 0);
            end
            busy <= 1'b1;
        end else if (output_consumed) begin
            busy       <= 1'b0;
            packed_sig <= packed_sig;
        end
    end

endmodule
