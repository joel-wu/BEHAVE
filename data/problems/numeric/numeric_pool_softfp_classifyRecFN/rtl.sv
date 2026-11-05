module recoded_fp32_classifier (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [32:0] in_recfn,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [9:0]  out_class
);

logic busy;

wire input_accepted;
wire output_consumed;

logic sign;
logic [8:0] exp_rec;
logic [22:0] fract;
logic [2:0] exp_top3;
logic isZero;
logic isInf;
logic isNaN;
logic isFiniteNonzero;
logic isSigNaN;
logic [8:0] sExp;
logic isSubnormal;
logic [9:0] class_next;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid = rst ? 1'b0 : busy;

always_comb begin
    sign = in_recfn[32];
    exp_rec = in_recfn[31:23];
    fract = in_recfn[22:0];
    exp_top3 = exp_rec[8:6];

    isZero = (exp_top3 == 3'b000);
    isInf = (exp_top3 == 3'b110);
    isNaN = (exp_top3 == 3'b111);
    isFiniteNonzero = (!isZero) && (!isInf) && (!isNaN);

    isSigNaN = isNaN && (fract[22] == 1'b0);

    sExp = exp_rec;
    isSubnormal = (sExp < 9'd130);

    class_next[9] = isNaN && (!isSigNaN);
    class_next[8] = isSigNaN;
    class_next[7] = (!sign) && isInf;
    class_next[6] = (!sign) && isFiniteNonzero && (!isSubnormal);
    class_next[5] = (!sign) && isFiniteNonzero && isSubnormal;
    class_next[4] = (!sign) && isZero;
    class_next[3] = sign && isZero;
    class_next[2] = sign && isFiniteNonzero && isSubnormal;
    class_next[1] = sign && isFiniteNonzero && (!isSubnormal);
    class_next[0] = sign && isInf;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_class <= 10'b0000000000;
    end else if (input_accepted) begin
        out_class <= class_next;
        busy      <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
