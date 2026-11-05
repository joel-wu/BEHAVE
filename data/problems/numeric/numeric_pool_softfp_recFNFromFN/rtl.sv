module rec_fn_from_fn32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_fn,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [32:0] out_recfn
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic        sign;
logic [7:0]  exp_in;
logic [22:0] fract_in;

logic        isZero;
logic        isNaN;
logic        isSubnormal;
logic        isNormal;

logic [4:0]  k;
logic [23:0] sig;
logic [8:0]  sExp;
logic [8:0]  recExp;
logic [22:0] recSig;
logic [32:0] recfn_next;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sign       = in_fn[31];
    exp_in     = in_fn[30:23];
    fract_in   = in_fn[22:0];

    isZero      = (exp_in == 8'h00) && (fract_in == 23'h000000);
    isNaN       = (exp_in == 8'hFF) && (fract_in != 23'h000000);
    isSubnormal = (exp_in == 8'h00) && (fract_in != 23'h000000);
    isNormal    = (exp_in != 8'h00) && (exp_in != 8'hFF);

    unique casez (fract_in)
        23'b1??????????????????????: k = 5'd0;
        23'b01?????????????????????: k = 5'd1;
        23'b001????????????????????: k = 5'd2;
        23'b0001???????????????????: k = 5'd3;
        23'b00001??????????????????: k = 5'd4;
        23'b000001?????????????????: k = 5'd5;
        23'b0000001????????????????: k = 5'd6;
        23'b00000001???????????????: k = 5'd7;
        23'b000000001??????????????: k = 5'd8;
        23'b0000000001?????????????: k = 5'd9;
        23'b00000000001????????????: k = 5'd10;
        23'b000000000001???????????: k = 5'd11;
        23'b0000000000001??????????: k = 5'd12;
        23'b00000000000001?????????: k = 5'd13;
        23'b000000000000001????????: k = 5'd14;
        23'b0000000000000001???????: k = 5'd15;
        23'b00000000000000001??????: k = 5'd16;
        23'b000000000000000001?????: k = 5'd17;
        23'b0000000000000000001????: k = 5'd18;
        23'b00000000000000000001???: k = 5'd19;
        23'b000000000000000000001??: k = 5'd20;
        23'b0000000000000000000001?: k = 5'd21;
        23'b00000000000000000000001: k = 5'd22;
        23'b00000000000000000000000: k = 5'd0;
    endcase

    if (isZero) begin
        sig  = 24'h000000;
        sExp = 9'h000;
    end else if (isNormal) begin
        sig  = {1'b1, fract_in};
        sExp = {1'b0, exp_in} + 9'd129;
    end else if (isSubnormal) begin
        sig  = ({1'b0, fract_in} << (k + 1)) & 24'hFFFFFF;
        sExp = 9'd129 - {4'b0, k};
    end else begin
        sig  = {1'b1, fract_in};
        sExp = 9'b110000000;
    end

    if (isZero) begin
        recExp = 9'h000;
    end else begin
        recExp = {sExp[8:6] | {2'b00, isNaN}, sExp[5:0]};
    end

    recSig    = sig[22:0];
    recfn_next = {sign, recExp, recSig};
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_recfn <= 33'h000000000;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_recfn <= recfn_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
