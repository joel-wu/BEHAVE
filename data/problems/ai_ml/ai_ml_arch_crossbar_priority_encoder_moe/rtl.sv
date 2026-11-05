module ai_ml_arch_crossbar_priority_encoder_moe (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_req,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_found,
    output logic [2:0]  out_index,
    output logic [7:0]  out_grant
);

logic        busy;
logic        next_found;
logic [2:0]  next_index;
logic [7:0]  next_grant;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    next_found = 1'b0;
    next_index = 3'b000;
    next_grant = 8'b00000000;

    if (in_req[0]) begin
        next_found = 1'b1;
        next_index = 3'd0;
        next_grant = 8'b00000001;
    end else if (in_req[1]) begin
        next_found = 1'b1;
        next_index = 3'd1;
        next_grant = 8'b00000010;
    end else if (in_req[2]) begin
        next_found = 1'b1;
        next_index = 3'd2;
        next_grant = 8'b00000100;
    end else if (in_req[3]) begin
        next_found = 1'b1;
        next_index = 3'd3;
        next_grant = 8'b00001000;
    end else if (in_req[4]) begin
        next_found = 1'b1;
        next_index = 3'd4;
        next_grant = 8'b00010000;
    end else if (in_req[5]) begin
        next_found = 1'b1;
        next_index = 3'd5;
        next_grant = 8'b00100000;
    end else if (in_req[6]) begin
        next_found = 1'b1;
        next_index = 3'd6;
        next_grant = 8'b01000000;
    end else if (in_req[7]) begin
        next_found = 1'b1;
        next_index = 3'd7;
        next_grant = 8'b10000000;
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy      <= 1'b0;
        out_found <= 1'b0;
        out_index <= 3'b000;
        out_grant <= 8'b00000000;
    end else begin
        if (input_accepted) begin
            busy      <= 1'b1;
            out_found <= next_found;
            out_index <= next_index;
            out_grant <= next_grant;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

endmodule