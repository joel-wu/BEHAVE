module FalconHashToPointStep (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_w,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [13:0] out_x
);

logic busy;

wire input_accepted;
wire output_consumed;
logic accept_candidate;
logic [13:0] reduced_x;

assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;
assign input_accepted  = in_valid & in_ready;

always_comb begin
    accept_candidate = 1'b0;
    reduced_x        = 14'd0;

    if (in_w < 16'd61445) begin
        accept_candidate = 1'b1;
        if (in_w < 16'd12289) begin
            reduced_x = in_w[13:0];
        end else if (in_w < 16'd24578) begin
            reduced_x = in_w - 16'd12289;
        end else if (in_w < 16'd36867) begin
            reduced_x = in_w - 16'd24578;
        end else if (in_w < 16'd49156) begin
            reduced_x = in_w - 16'd36867;
        end else begin
            reduced_x = in_w - 16'd49156;
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_x <= 14'd0;
    end else if (input_accepted) begin
        if (accept_candidate) begin
            busy  <= 1'b1;
            out_x <= reduced_x;
        end else begin
            busy  <= 1'b0;
            out_x <= out_x;
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule