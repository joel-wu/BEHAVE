module q15_fir_decimator (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);

logic [15:0] state0;
logic [15:0] state1;
logic [15:0] state2;
logic [15:0] state3;
logic [15:0] state4;
logic [15:0] state5;
logic [15:0] state6;
logic        phase;
logic [15:0] saved;
logic        pending_output;

wire input_accepted  = in_valid & in_ready;
wire output_consumed = out_valid & out_ready;

assign in_ready  = ~pending_output | output_consumed;
assign out_valid = pending_output;

logic signed [31:0] acc;
logic signed [31:0] shifted;
logic signed [15:0] saturated;

always_comb begin
    acc =
        ($signed(state0)        * 32'sd4096) +
        ($signed(state1)        * 32'sd4096) +
        ($signed(state2)        * 32'sd4096) +
        ($signed(state3)        * 32'sd4096) +
        ($signed(state4)        * 32'sd4096) +
        ($signed(state5)        * 32'sd4096) +
        ($signed(state6)        * 32'sd4096) +
        ($signed(in_sample)     * 32'sd4096);
    shifted = acc >>> 15;
    if (shifted > 32'sd32767) begin
        saturated = 16'sd32767;
    end else if (shifted < -32'sd32768) begin
        saturated = -16'sd32768;
    end else begin
        saturated = shifted[15:0];
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        state0          <= 16'h0000;
        state1          <= 16'h0000;
        state2          <= 16'h0000;
        state3          <= 16'h0000;
        state4          <= 16'h0000;
        state5          <= 16'h0000;
        state6          <= 16'h0000;
        phase           <= 1'b0;
        saved           <= 16'h0000;
        pending_output  <= 1'b0;
        out_sample      <= 16'h0000;
    end else begin
        if (input_accepted) begin
            state0 <= state1;
            state1 <= state2;
            state2 <= state3;
            state3 <= state4;
            state4 <= state5;
            state5 <= state6;
            state6 <= in_sample;
            phase  <= ~phase;

            if (!phase) saved <= saturated;
            if (phase) begin
                pending_output <= 1'b1;
                out_sample     <= saved;
            end else begin
                pending_output <= 1'b0;
            end
        end else if (output_consumed) begin
            pending_output <= 1'b0;
        end
    end
end

endmodule
