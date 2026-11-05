module data_systems_sort_rank_bitonic_sort_8_int32 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_x0,
    input  logic [31:0]  in_x1,
    input  logic [31:0]  in_x2,
    input  logic [31:0]  in_x3,
    input  logic [31:0]  in_x4,
    input  logic [31:0]  in_x5,
    input  logic [31:0]  in_x6,
    input  logic [31:0]  in_x7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x0,
    output logic [31:0]  out_x1,
    output logic [31:0]  out_x2,
    output logic [31:0]  out_x3,
    output logic [31:0]  out_x4,
    output logic [31:0]  out_x5,
    output logic [31:0]  out_x6,
    output logic [31:0]  out_x7
);

    logic pending;
    assign in_ready = !pending || out_ready;
    assign out_valid = pending;
    wire signed [31:0] lane [0:6][0:7];
    assign lane[0][0] = $signed(in_x0);
    assign lane[0][1] = $signed(in_x1);
    assign lane[0][2] = $signed(in_x2);
    assign lane[0][3] = $signed(in_x3);
    assign lane[0][4] = $signed(in_x4);
    assign lane[0][5] = $signed(in_x5);
    assign lane[0][6] = $signed(in_x6);
    assign lane[0][7] = $signed(in_x7);
    for (genvar i0 = 0; i0 < 8; i0++) begin : stage_0
        localparam integer PARTNER = i0 ^ 1;
        localparam bit TAKE_MIN = ((i0 & 2) == 0) == ((i0 & 1) == 0);
        assign lane[1][i0] = (($signed(lane[0][i0]) < $signed(lane[0][PARTNER])) == TAKE_MIN)
            ? lane[0][i0] : lane[0][PARTNER];
    end
    for (genvar i1 = 0; i1 < 8; i1++) begin : stage_1
        localparam integer PARTNER = i1 ^ 2;
        localparam bit TAKE_MIN = ((i1 & 4) == 0) == ((i1 & 2) == 0);
        assign lane[2][i1] = (($signed(lane[1][i1]) < $signed(lane[1][PARTNER])) == TAKE_MIN)
            ? lane[1][i1] : lane[1][PARTNER];
    end
    for (genvar i2 = 0; i2 < 8; i2++) begin : stage_2
        localparam integer PARTNER = i2 ^ 1;
        localparam bit TAKE_MIN = ((i2 & 4) == 0) == ((i2 & 1) == 0);
        assign lane[3][i2] = (($signed(lane[2][i2]) < $signed(lane[2][PARTNER])) == TAKE_MIN)
            ? lane[2][i2] : lane[2][PARTNER];
    end
    for (genvar i3 = 0; i3 < 8; i3++) begin : stage_3
        localparam integer PARTNER = i3 ^ 4;
        localparam bit TAKE_MIN = ((i3 & 8) == 0) == ((i3 & 4) == 0);
        assign lane[4][i3] = (($signed(lane[3][i3]) < $signed(lane[3][PARTNER])) == TAKE_MIN)
            ? lane[3][i3] : lane[3][PARTNER];
    end
    for (genvar i4 = 0; i4 < 8; i4++) begin : stage_4
        localparam integer PARTNER = i4 ^ 2;
        localparam bit TAKE_MIN = ((i4 & 8) == 0) == ((i4 & 2) == 0);
        assign lane[5][i4] = (($signed(lane[4][i4]) < $signed(lane[4][PARTNER])) == TAKE_MIN)
            ? lane[4][i4] : lane[4][PARTNER];
    end
    for (genvar i5 = 0; i5 < 8; i5++) begin : stage_5
        localparam integer PARTNER = i5 ^ 1;
        localparam bit TAKE_MIN = ((i5 & 8) == 0) == ((i5 & 1) == 0);
        assign lane[6][i5] = (($signed(lane[5][i5]) < $signed(lane[5][PARTNER])) == TAKE_MIN)
            ? lane[5][i5] : lane[5][PARTNER];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 1'b0;
            out_x0 <= '0;
            out_x1 <= '0;
            out_x2 <= '0;
            out_x3 <= '0;
            out_x4 <= '0;
            out_x5 <= '0;
            out_x6 <= '0;
            out_x7 <= '0;
        end else if (in_valid && in_ready) begin
            pending <= 1'b1;
            out_x0 <= lane[6][0];
            out_x1 <= lane[6][1];
            out_x2 <= lane[6][2];
            out_x3 <= lane[6][3];
            out_x4 <= lane[6][4];
            out_x5 <= lane[6][5];
            out_x6 <= lane[6][6];
            out_x7 <= lane[6][7];
        end else if (out_ready) begin
            pending <= 1'b0;
        end
    end
endmodule
