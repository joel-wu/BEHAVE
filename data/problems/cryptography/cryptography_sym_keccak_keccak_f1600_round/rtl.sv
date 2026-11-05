module keccak_f1600_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [4:0]   in_round_idx,
    input  logic [63:0]  in_x0,
    input  logic [63:0]  in_x1,
    input  logic [63:0]  in_x2,
    input  logic [63:0]  in_x3,
    input  logic [63:0]  in_x4,
    input  logic [63:0]  in_x5,
    input  logic [63:0]  in_x6,
    input  logic [63:0]  in_x7,
    input  logic [63:0]  in_x8,
    input  logic [63:0]  in_x9,
    input  logic [63:0]  in_x10,
    input  logic [63:0]  in_x11,
    input  logic [63:0]  in_x12,
    input  logic [63:0]  in_x13,
    input  logic [63:0]  in_x14,
    input  logic [63:0]  in_x15,
    input  logic [63:0]  in_x16,
    input  logic [63:0]  in_x17,
    input  logic [63:0]  in_x18,
    input  logic [63:0]  in_x19,
    input  logic [63:0]  in_x20,
    input  logic [63:0]  in_x21,
    input  logic [63:0]  in_x22,
    input  logic [63:0]  in_x23,
    input  logic [63:0]  in_x24,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x0,
    output logic [63:0]  out_x1,
    output logic [63:0]  out_x2,
    output logic [63:0]  out_x3,
    output logic [63:0]  out_x4,
    output logic [63:0]  out_x5,
    output logic [63:0]  out_x6,
    output logic [63:0]  out_x7,
    output logic [63:0]  out_x8,
    output logic [63:0]  out_x9,
    output logic [63:0]  out_x10,
    output logic [63:0]  out_x11,
    output logic [63:0]  out_x12,
    output logic [63:0]  out_x13,
    output logic [63:0]  out_x14,
    output logic [63:0]  out_x15,
    output logic [63:0]  out_x16,
    output logic [63:0]  out_x17,
    output logic [63:0]  out_x18,
    output logic [63:0]  out_x19,
    output logic [63:0]  out_x20,
    output logic [63:0]  out_x21,
    output logic [63:0]  out_x22,
    output logic [63:0]  out_x23,
    output logic [63:0]  out_x24
);

function automatic logic [63:0] ror64(input logic [63:0] v, input logic [5:0] s);
    logic [63:0] right_part;
    logic [63:0] left_part;
    logic [5:0]  ls;
begin
    ls = (6'd64 - s) & 6'h3f;
    right_part = v >> s;
    left_part  = v << ls;
    ror64 = right_part | left_part;
end
endfunction

function automatic logic [63:0] round_constant(input logic [4:0] idx);
begin
    round_constant = 64'h0000000000000000;
    case (idx)
        5'd0:  round_constant = 64'h0000000000000001;
        5'd1:  round_constant = 64'h0000000000008082;
        5'd2:  round_constant = 64'h800000000000808A;
        5'd3:  round_constant = 64'h8000000080008000;
        5'd4:  round_constant = 64'h000000000000808B;
        5'd5:  round_constant = 64'h0000000080000001;
        5'd6:  round_constant = 64'h8000000080008081;
        5'd7:  round_constant = 64'h8000000000008009;
        5'd8:  round_constant = 64'h000000000000008A;
        5'd9:  round_constant = 64'h0000000000000088;
        5'd10: round_constant = 64'h0000000080008009;
        5'd11: round_constant = 64'h000000008000000A;
        5'd12: round_constant = 64'h000000008000808B;
        5'd13: round_constant = 64'h800000000000008B;
        5'd14: round_constant = 64'h8000000000008089;
        5'd15: round_constant = 64'h8000000000008003;
        5'd16: round_constant = 64'h8000000000008002;
        5'd17: round_constant = 64'h8000000000000080;
        5'd18: round_constant = 64'h000000000000800A;
        5'd19: round_constant = 64'h800000008000000A;
        5'd20: round_constant = 64'h8000000080008081;
        5'd21: round_constant = 64'h8000000000008080;
        5'd22: round_constant = 64'h0000000080000001;
        5'd23: round_constant = 64'h8000000080008008;
        default: round_constant = 64'h0000000000000000;
    endcase
end
endfunction

logic busy;
wire input_accepted;
wire output_consumed;

logic [63:0] nx0, nx1, nx2, nx3, nx4;
logic [63:0] nx5, nx6, nx7, nx8, nx9;
logic [63:0] nx10, nx11, nx12, nx13, nx14;
logic [63:0] nx15, nx16, nx17, nx18, nx19;
logic [63:0] nx20, nx21, nx22, nx23, nx24;

logic [63:0] x0c, x1c, x2c, x3c, x4c;
logic [63:0] x5c, x6c, x7c, x8c, x9c;
logic [63:0] x10c, x11c, x12c, x13c, x14c;
logic [63:0] x15c, x16c, x17c, x18c, x19c;
logic [63:0] x20c, x21c, x22c, x23c, x24c;

logic [63:0] y0, y1, y2, y3, y4, t;
logic [63:0] rc;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    y4 = in_x4 ^ in_x9 ^ in_x14 ^ in_x19 ^ in_x24;
    y1 = in_x1 ^ in_x6 ^ in_x11 ^ in_x16 ^ in_x21;
    y3 = in_x3 ^ in_x8 ^ in_x13 ^ in_x18 ^ in_x23;
    y0 = in_x0 ^ in_x5 ^ in_x10 ^ in_x15 ^ in_x20;
    y2 = in_x2 ^ in_x7 ^ in_x12 ^ in_x17 ^ in_x22;

    t  = ror64(y4, 6'd63);
    y4 = y4 ^ ror64(y1, 6'd63);
    y1 = y1 ^ ror64(y3, 6'd63);
    y3 = y3 ^ ror64(y0, 6'd63);
    y0 = y0 ^ ror64(y2, 6'd63);
    y2 = y2 ^ t;

    x0c  = in_x0  ^ y4;
    x1c  = in_x1  ^ y0;
    x2c  = in_x2  ^ y1;
    x3c  = in_x3  ^ y2;
    x4c  = in_x4  ^ y3;
    x5c  = in_x5  ^ y4;
    x6c  = in_x6  ^ y0;
    x7c  = in_x7  ^ y1;
    x8c  = in_x8  ^ y2;
    x9c  = in_x9  ^ y3;
    x10c = in_x10 ^ y4;
    x11c = in_x11 ^ y0;
    x12c = in_x12 ^ y1;
    x13c = in_x13 ^ y2;
    x14c = in_x14 ^ y3;
    x15c = in_x15 ^ y4;
    x16c = in_x16 ^ y0;
    x17c = in_x17 ^ y1;
    x18c = in_x18 ^ y2;
    x19c = in_x19 ^ y3;
    x20c = in_x20 ^ y4;
    x21c = in_x21 ^ y0;
    x22c = in_x22 ^ y1;
    x23c = in_x23 ^ y2;
    x24c = in_x24 ^ y3;

    t    = ror64(x1c, 6'd63);
    x1c  = ror64(x6c, 6'd20);
    x6c  = ror64(x9c, 6'd44);
    x9c  = ror64(x22c, 6'd3);
    x22c = ror64(x14c, 6'd25);
    x14c = ror64(x20c, 6'd46);
    x20c = ror64(x2c, 6'd2);
    x2c  = ror64(x12c, 6'd21);
    x12c = ror64(x13c, 6'd39);
    x13c = ror64(x19c, 6'd56);
    x19c = ror64(x23c, 6'd8);
    x23c = ror64(x15c, 6'd23);
    x15c = ror64(x4c, 6'd37);
    x4c  = ror64(x24c, 6'd50);
    x24c = ror64(x21c, 6'd62);
    x21c = ror64(x8c, 6'd9);
    x8c  = ror64(x16c, 6'd19);
    x16c = ror64(x5c, 6'd28);
    x5c  = ror64(x3c, 6'd36);
    x3c  = ror64(x18c, 6'd43);
    x18c = ror64(x17c, 6'd49);
    x17c = ror64(x11c, 6'd54);
    x11c = ror64(x7c, 6'd58);
    x7c  = ror64(x10c, 6'd61);
    x10c = t;

    t   = x4c & (~x3c);
    x4c = x4c ^ (x1c & (~x0c));
    x1c = x1c ^ (x3c & (~x2c));
    x3c = x3c ^ (x0c & (~x4c));
    x0c = x0c ^ (x2c & (~x1c));
    x2c = x2c ^ t;

    t   = x9c & (~x8c);
    x9c = x9c ^ (x6c & (~x5c));
    x6c = x6c ^ (x8c & (~x7c));
    x8c = x8c ^ (x5c & (~x9c));
    x5c = x5c ^ (x7c & (~x6c));
    x7c = x7c ^ t;

    t    = x14c & (~x13c);
    x14c = x14c ^ (x11c & (~x10c));
    x11c = x11c ^ (x13c & (~x12c));
    x13c = x13c ^ (x10c & (~x14c));
    x10c = x10c ^ (x12c & (~x11c));
    x12c = x12c ^ t;

    t    = x19c & (~x18c);
    x19c = x19c ^ (x16c & (~x15c));
    x16c = x16c ^ (x18c & (~x17c));
    x18c = x18c ^ (x15c & (~x19c));
    x15c = x15c ^ (x17c & (~x16c));
    x17c = x17c ^ t;

    t    = x24c & (~x23c);
    x24c = x24c ^ (x21c & (~x20c));
    x21c = x21c ^ (x23c & (~x22c));
    x23c = x23c ^ (x20c & (~x24c));
    x20c = x20c ^ (x22c & (~x21c));
    x22c = x22c ^ t;

    rc = round_constant(in_round_idx);

    nx0  = x0c ^ rc;
    nx1  = x1c;
    nx2  = x2c;
    nx3  = x3c;
    nx4  = x4c;
    nx5  = x5c;
    nx6  = x6c;
    nx7  = x7c;
    nx8  = x8c;
    nx9  = x9c;
    nx10 = x10c;
    nx11 = x11c;
    nx12 = x12c;
    nx13 = x13c;
    nx14 = x14c;
    nx15 = x15c;
    nx16 = x16c;
    nx17 = x17c;
    nx18 = x18c;
    nx19 = x19c;
    nx20 = x20c;
    nx21 = x21c;
    nx22 = x22c;
    nx23 = x23c;
    nx24 = x24c;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x0  <= 64'd0;
        out_x1  <= 64'd0;
        out_x2  <= 64'd0;
        out_x3  <= 64'd0;
        out_x4  <= 64'd0;
        out_x5  <= 64'd0;
        out_x6  <= 64'd0;
        out_x7  <= 64'd0;
        out_x8  <= 64'd0;
        out_x9  <= 64'd0;
        out_x10 <= 64'd0;
        out_x11 <= 64'd0;
        out_x12 <= 64'd0;
        out_x13 <= 64'd0;
        out_x14 <= 64'd0;
        out_x15 <= 64'd0;
        out_x16 <= 64'd0;
        out_x17 <= 64'd0;
        out_x18 <= 64'd0;
        out_x19 <= 64'd0;
        out_x20 <= 64'd0;
        out_x21 <= 64'd0;
        out_x22 <= 64'd0;
        out_x23 <= 64'd0;
        out_x24 <= 64'd0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_x0  <= nx0;
        out_x1  <= nx1;
        out_x2  <= nx2;
        out_x3  <= nx3;
        out_x4  <= nx4;
        out_x5  <= nx5;
        out_x6  <= nx6;
        out_x7  <= nx7;
        out_x8  <= nx8;
        out_x9  <= nx9;
        out_x10 <= nx10;
        out_x11 <= nx11;
        out_x12 <= nx12;
        out_x13 <= nx13;
        out_x14 <= nx14;
        out_x15 <= nx15;
        out_x16 <= nx16;
        out_x17 <= nx17;
        out_x18 <= nx18;
        out_x19 <= nx19;
        out_x20 <= nx20;
        out_x21 <= nx21;
        out_x22 <= nx22;
        out_x23 <= nx23;
        out_x24 <= nx24;
    end else if (output_consumed) begin
        busy    <= 1'b0;
        out_x0  <= 64'd0;
        out_x1  <= 64'd0;
        out_x2  <= 64'd0;
        out_x3  <= 64'd0;
        out_x4  <= 64'd0;
        out_x5  <= 64'd0;
        out_x6  <= 64'd0;
        out_x7  <= 64'd0;
        out_x8  <= 64'd0;
        out_x9  <= 64'd0;
        out_x10 <= 64'd0;
        out_x11 <= 64'd0;
        out_x12 <= 64'd0;
        out_x13 <= 64'd0;
        out_x14 <= 64'd0;
        out_x15 <= 64'd0;
        out_x16 <= 64'd0;
        out_x17 <= 64'd0;
        out_x18 <= 64'd0;
        out_x19 <= 64'd0;
        out_x20 <= 64'd0;
        out_x21 <= 64'd0;
        out_x22 <= 64'd0;
        out_x23 <= 64'd0;
        out_x24 <= 64'd0;
    end
end

endmodule