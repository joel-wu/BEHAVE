module ai_ml_sample_pick_sample_streaming_topk (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_start,
    input  logic         in_last,
    input  logic [15:0]  in_token_id,
    input  logic [31:0]  in_score,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_token_id
);

    logic        frame_active;
    logic [15:0] best_token_id;
    logic [31:0] best_score;
    logic        busy;

    logic        next_frame_active;
    logic [15:0] next_best_token_id;
    logic [31:0] next_best_score;
    logic [15:0] emit_token_id;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        next_frame_active = frame_active;
        next_best_token_id = best_token_id;
        next_best_score = best_score;
        emit_token_id = 16'h0000;

        if (in_start) begin
            next_best_token_id = in_token_id;
            next_best_score = in_score;
            next_frame_active = 1'b1;
        end else if (frame_active) begin
            if (($signed(in_score) > $signed(best_score)) ||
                ((in_score == best_score) && (in_token_id < best_token_id))) begin
                next_best_token_id = in_token_id;
                next_best_score = in_score;
            end
        end

        if (in_last) begin
            if (next_frame_active) begin
                emit_token_id = next_best_token_id;
            end else begin
                emit_token_id = 16'h0000;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            frame_active  <= 1'b0;
            best_token_id <= 16'h0000;
            best_score    <= 32'h00000000;
            out_token_id  <= 16'h0000;
            busy          <= 1'b0;
        end else begin
            if (input_accepted) begin
                if (in_last) begin
                    out_token_id  <= emit_token_id;
                    busy          <= 1'b1;
                    frame_active  <= 1'b0;
                    best_token_id <= 16'h0000;
                    best_score    <= 32'h00000000;
                end else begin
                    frame_active  <= next_frame_active;
                    best_token_id <= next_best_token_id;
                    best_score    <= next_best_score;
                    busy          <= 1'b0;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
